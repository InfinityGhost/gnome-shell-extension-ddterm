import contextlib
import logging

from gi.repository import GLib, Gio

from . import dbus_proxy


LOGGER = logging.getLogger(__name__)

DBUS_INTROSPECTABLE_XML = '''
<!DOCTYPE node PUBLIC '-//freedesktop//DTD D-BUS Object Introspection 1.0//EN'
    'http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd'>
<node>
    <interface name='org.freedesktop.DBus.Introspectable'>
        <method name='Introspect'>
            <arg name='data' direction='out' type='s'/>
        </method>
    </interface>
</node>
'''


class IntrospectableProxy(dbus_proxy.DBusProxy):
    __introspection_xml__ = DBUS_INTROSPECTABLE_XML


@contextlib.contextmanager
def new_main_context():
    context = GLib.MainContext.new()
    context.push_thread_default()

    try:
        yield context

    finally:
        context.pop_thread_default()


@contextlib.contextmanager
def new_cancellable():
    cancellable = Gio.Cancellable()

    try:
        yield cancellable

    finally:
        cancellable.cancel()


class SourceContextManager(contextlib.AbstractContextManager):
    def __init__(self, source, context):
        self.source = source
        self.context = context

    def __enter__(self):
        self.source.attach(self.context)
        return self

    def __exit__(self, *_):
        if not self.source.is_destroyed():
            self.source.destroy()


class Timeout(SourceContextManager):
    def __init__(self, interval, context, callback=None):
        self.timed_out = False
        self.callback = callback

        super().__init__(GLib.timeout_source_new(interval), context)
        self.source.set_callback(self._callback)

    def _callback(self, *_):
        self.timed_out = True

        if self.callback:
            self.callback()

        return GLib.SOURCE_REMOVE


def _fix_exception_context(new_exc, old_exc):
    while True:
        exc_context = new_exc.__context__

        if exc_context is old_exc:
            return

        if exc_context is None:
            break

        new_exc = exc_context

    new_exc.__context__ = old_exc


class SetError(contextlib.AbstractContextManager, contextlib.ContextDecorator):
    def __init__(self, loop):
        self.exception = None
        self.loop = loop

    def reraise(self):
        if self.exception is None:
            return

        fixed_ctx = self.exception.__context__

        try:
            raise self.exception

        except BaseException as ex:
            ex.__context__ = fixed_ctx
            raise

    def __exit__(self, _, exception, traceback):
        if exception is None:
            return False

        if isinstance(exception, GLib.Error):
            if exception.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                return True

        if self.exception is None:
            self.exception = exception
        else:
            _fix_exception_context(exception, self.exception)
            self.exception = exception

        self.loop.quit()
        return True


class CommonContext(contextlib.ExitStack):
    def __init__(self, timeout_ms):
        super().__init__()
        self.loop = GLib.MainLoop.new(GLib.MainContext.get_thread_default(), False)
        self.timeout = self.make_timeout(timeout_ms, self.loop.quit)
        self.set_error = SetError(self.loop)

    def reraise(self):
        self.set_error.reraise()

        if self.timeout.timed_out:
            raise TimeoutError()

    def __enter__(self):
        super().__enter__()
        self.enter_context(self.timeout)
        return self

    def __exit__(self, *exc_info):
        try:
            self.reraise()
        finally:
            return super().__exit__(*exc_info)

    def make_timeout(self, timeout_ms, callback=None):
        return Timeout(timeout_ms, self.loop.get_context(), callback=callback)


def wait_dbus_interface(connection, dest, path, interface, timeout_ms, proxy_class=None):
    if proxy_class is not None:
        assert interface == proxy_class.__interface_info__.name

    with CommonContext(timeout_ms) as cm:
        proxy_cm = cm.enter_context(contextlib.ExitStack())
        proxy = None

        @cm.set_error
        def proxy_ready_cb(source, res):
            try:
                source.init_finish(res)

            except GLib.Error:
                LOGGER.exception(
                    'Failed to create %r proxy for dest=%r path=%r',
                    interface,
                    dest,
                    path
                )
                raise

            LOGGER.info(
                'Created %r proxy for dest=%r, path=%r',
                source.props.g_interface_name,
                source.props.g_name_owner,
                source.props.g_object_path
            )

            nonlocal proxy
            proxy = source

            cm.loop.quit()

        @cm.set_error
        def introspect_done_cb(proxy, res):
            LOGGER.debug(
                '%s.Introspect() call complete for dest=%r, path=%r',
                proxy.props.g_interface_name,
                proxy.props.g_name_owner,
                proxy.props.g_object_path
            )

            try:
                (xml,) = proxy.call_finish(res).unpack()

            except GLib.Error:
                LOGGER.exception(
                    '%s.Introspect() failed for dest=%r path=%r',
                    proxy.props.g_interface_name,
                    proxy.props.g_name_owner,
                    proxy.props.g_object_path
                )
                raise

            try:
                interface_info = Gio.DBusNodeInfo.new_for_xml(xml).lookup_interface(interface)

            except GLib.Error:
                LOGGER.exception(
                    'Failed to parse introspection XML for dest=%r path=%r',
                    proxy.props.g_name_owner,
                    proxy.props.g_object_path
                )
                raise

            if not interface_info:
                LOGGER.debug(
                    'Interface %r not found for dest=%r, path=%r',
                    interface,
                    proxy.props.g_name_owner,
                    proxy.props.g_object_path
                )

                proxy_cm.close()
                proxy_cm.enter_context(cm.make_timeout(100, lambda: introspect(proxy)))
                return

            LOGGER.debug(
                'Trying to create %r proxy for dest=%r, path=%r',
                interface,
                dest,
                path
            )

            cancellable = proxy_cm.enter_context(new_cancellable())
            if proxy_class is None:
                proxy = Gio.DBusProxy(
                    g_connection=connection,
                    g_name=dest,
                    g_object_path=path,
                    g_interface_info=interface_info,
                    g_interface_name=interface,
                    g_flags=Gio.DBusProxyFlags.GET_INVALIDATED_PROPERTIES
                )
            else:
                proxy = proxy_class(
                    g_connection=connection,
                    g_name=dest,
                    g_object_path=path
                )

            proxy.init_async(
                GLib.PRIORITY_DEFAULT,
                cancellable,
                proxy_ready_cb
            )

        def introspect(proxy):
            LOGGER.debug(
                'Calling %s.Introspect() for dest=%r, path=%r',
                proxy.props.g_interface_name,
                proxy.props.g_name_owner,
                proxy.props.g_object_path
            )

            cancellable = proxy_cm.enter_context(new_cancellable())
            proxy.Introspect(
                _timeout_msec=timeout_ms,
                _cancellable=cancellable,
                _callback=introspect_done_cb
            )

        @cm.set_error
        def introspectable_proxy_ready_cb(proxy, res):
            try:
                proxy.init_finish(res)

            except GLib.Error:
                LOGGER.exception('Failed to create %r proxy for dest=%r path=%r', proxy.__interface_info__.name, dest, path)
                raise

            LOGGER.info(
                'Created %r proxy for dest=%r, path=%r',
                proxy.props.g_interface_name,
                proxy.props.g_name_owner,
                proxy.props.g_object_path
            )

            introspect(proxy)

        def try_create_introspectable_proxy():
            proxy_cm.close()

            LOGGER.debug('Trying to create %r proxy for dest=%r, path=%r', IntrospectableProxy.__interface_info__.name, dest, path)
            cancellable = proxy_cm.enter_context(new_cancellable())
            proxy = IntrospectableProxy(
                g_connection=connection,
                g_name=dest,
                g_object_path=path
            )
            proxy.init_async(
                GLib.PRIORITY_DEFAULT,
                cancellable,
                introspectable_proxy_ready_cb
            )

        cm.callback(Gio.bus_unwatch_name, Gio.bus_watch_name_on_connection(
            connection,
            dest,
            Gio.BusNameWatcherFlags.NONE,
            lambda *_: try_create_introspectable_proxy(),
            lambda *_: proxy_cm.close()
        ))

        cm.loop.run()
        return proxy


def wait_dbus_connection(address, timeout_ms):
    with CommonContext(timeout_ms) as cm:
        connection_cm = cm.enter_context(contextlib.ExitStack())
        connection = None

        @cm.set_error
        def connection_ready_cb(_, res):
            try:
                nonlocal connection
                connection = Gio.DBusConnection.new_for_address_finish(res)
                cm.loop.quit()

            except GLib.Error as ex:
                if ex.matches(Gio.io_error_quark(), Gio.IOErrorEnum.BROKEN_PIPE):
                    LOGGER.debug('Failed to connect to DBus address %r, trying again', address, exc_info=True)
                    connection_cm.close()
                    connection_cm.enter_context(cm.make_timeout(100, try_connect))
                else:
                    LOGGER.exception('Failed to connect to DBus address %r', address)
                    raise

        def try_connect():
            connection_cm.close()

            LOGGER.debug('Trying to connect to DBus address %r', address)
            cancellable = connection_cm.enter_context(new_cancellable())
            Gio.DBusConnection.new_for_address(
                address,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None,
                cancellable,
                connection_ready_cb
            )

        try_connect()

        cm.loop.run()
        return connection


def wait_property_value(object, property, value, timeout_ms):
    with CommonContext(timeout_ms) as cm:
        @cm.set_error
        def check(*_):
            if value == object.get_property(property):
                cm.loop.quit()

        handler = object.connect(f'notify::{property}', check)
        cm.callback(object.disconnect, handler)

        if value != object.get_property(property):
            cm.loop.run()
