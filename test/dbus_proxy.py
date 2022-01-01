import collections
import inspect
import sys
import types
import typing

from gi.repository import GLib, Gio, GObject


TypeInfo = collections.namedtuple('TypeInfo', ['accessor', 'constructor', 'pytype', 'pspec'])


def unsigned_max(bits):
    return 2 ** bits - 1


def signed_max(bits):
    return 2 ** (bits - 1) - 1


def signed_min(bits):
    return -(2 ** (bits - 1))


def unsigned_pspec(bits):
    return signed_min(bits), signed_max(bits), 0


def signed_pspec(bits):
    return 0, unsigned_max(bits), 0


VARIANT_TYPE = TypeInfo(
    accessor=GLib.Variant.get_variant,
    constructor=GLib.Variant.new_variant,
    pytype=GLib.Variant,
    pspec=(None,),
)

TYPES = {
    'b': TypeInfo(
        accessor=GLib.Variant.get_boolean,
        constructor=GLib.Variant.new_boolean,
        pytype=bool,
        pspec=(False,)
    ),
    'y': TypeInfo(
        accessor=GLib.Variant.get_byte,
        constructor=GLib.Variant.new_byte,
        pytype=int,
        pspec=unsigned_pspec(8),
    ),
    'n': TypeInfo(
        accessor=GLib.Variant.get_int16,
        constructor=GLib.Variant.new_int16,
        pytype=int,
        pspec=signed_pspec(16),
    ),
    'q': TypeInfo(
        accessor=GLib.Variant.get_uint16,
        constructor=GLib.Variant.new_uint16,
        pytype=int,
        pspec=unsigned_pspec(16),
    ),
    'i': TypeInfo(
        accessor=GLib.Variant.get_int32,
        constructor=GLib.Variant.new_int32,
        pytype=int,
        pspec=signed_pspec(32),
    ),
    'u': TypeInfo(
        accessor=GLib.Variant.get_uint32,
        constructor=GLib.Variant.new_uint32,
        pytype=int,
        pspec=unsigned_pspec(32),
    ),
    'x': TypeInfo(
        accessor=GLib.Variant.get_int64,
        constructor=GLib.Variant.new_int64,
        pytype=int,
        pspec=signed_pspec(64),
    ),
    't': TypeInfo(
        accessor=GLib.Variant.get_uint64,
        constructor=GLib.Variant.new_uint64,
        pytype=int,
        pspec=unsigned_pspec(64),
    ),
    'd': TypeInfo(
        accessor=GLib.Variant.get_double,
        constructor=GLib.Variant.new_double,
        pytype=float,
        pspec=(-sys.float_info.max, sys.float_info.max, 0.0),
    ),
    's': TypeInfo(
        accessor=GLib.Variant.get_string,
        constructor=GLib.Variant.new_string,
        pytype=str,
        pspec=(None,),
    ),
    'o': TypeInfo(
        accessor=GLib.Variant.get_string,
        constructor=GLib.Variant.new_object_path,
        pytype=str,
        pspec=(None,),
    ),
    'g': TypeInfo(
        accessor=GLib.Variant.get_string,
        constructor=GLib.Variant.new_signature,
        pytype=str,
        pspec=(None,),
    ),
    'v': VARIANT_TYPE,
}


def unpack(variant):
    t = TYPES.get(variant.get_type_string())
    if t:
        return t.accessor(variant)

    return variant


def pack(signature, data):
    t = TYPES.get(signature)
    if t:
        return t.constructor(data)

    return data


def unpack_type(signature):
    return TYPES.get(signature, VARIANT_TYPE)


def interface_info_from_xml(xml):
    node_info = Gio.DBusNodeInfo.new_for_xml(xml)

    if node_info.nodes:
        raise ValueError('Expected exactly one interface under root node, got child nodes')

    if node_info.annotations:
        raise ValueError('Expected exactly one interface under root node, got annotations for root node')

    if len(node_info.interfaces) != 1:
        raise ValueError(f'Expected exactly one interface under root node, got {len(node_info.interfaces)} interfaces')

    return node_info.interfaces[0]


def parameter_from_arg_info(arg_info):
    return inspect.Parameter(
        name=arg_info.name,
        kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
        annotation=unpack_type(arg_info.signature).pytype
    )


DBUS_METHOD_EXTRA_ARGS_PRE = [inspect.Parameter(
    name='_proxy',
    kind=inspect.Parameter.POSITIONAL_ONLY,
    annotation=Gio.DBusProxy
)]

DBUS_METHOD_EXTRA_ARGS_POST = [
    inspect.Parameter(
        name='_cancellable',
        kind=inspect.Parameter.KEYWORD_ONLY,
        annotation=typing.Optional[Gio.Cancellable],
        default=None
    ),
    inspect.Parameter(
        name='_flags',
        kind=inspect.Parameter.KEYWORD_ONLY,
        annotation=Gio.DBusCallFlags,
        default=Gio.DBusCallFlags.NONE
    ),
    inspect.Parameter(
        name='_timeout_msec',
        kind=inspect.Parameter.KEYWORD_ONLY,
        annotation=int,
        default=-1
    ),
    inspect.Parameter(
        name='_callback',
        kind=inspect.Parameter.KEYWORD_ONLY,
        default=None
    ),
]


def signature_from_method_info(method_info):
    return inspect.Signature(parameters=(
        DBUS_METHOD_EXTRA_ARGS_PRE +
        [parameter_from_arg_info(arg) for arg in method_info.in_args] +
        DBUS_METHOD_EXTRA_ARGS_POST
    ))


def unpack_tuple(variant):
    return tuple(
        unpack(variant.get_child_value(i))
        for i in range(len(variant))
    )


def unpack_return(variant):
    if variant is None:
        return None

    unpacked = unpack_tuple(variant)
    if len(unpacked) == 1:
        return unpacked[0]

    return unpacked


class DBusMethod:
    def __init__(self, method_info):
        self.method_info = method_info
        self.__signature__ = signature_from_method_info(method_info)

    def __get__(self, instance, _=None):
        if instance is None:
            return self

        return types.MethodType(self, instance)

    def __call__(self, *args, **kwargs):
        bound_args = self.__signature__.bind(*args, **kwargs)
        bound_args.apply_defaults()

        instance = bound_args.arguments['_proxy']
        callback = bound_args.arguments['_callback']

        base_args = (
            self.method_info.name,
            GLib.Variant.new_tuple([
                bound_args.arguments[arg.name] for arg in self.method_info.in_args
            ]) if self.method_info.in_args else None,
            bound_args.arguments['_flags'],
            bound_args.arguments['_timeout_msec'],
            bound_args.arguments['_cancellable']
        )

        if callback is None:
            return unpack_return(instance.call_sync(*base_args))

        instance.call(*base_args, callback)
        return None


class DBusProxy(Gio.DBusProxy):
    def __init_subclass__(cls, **kwargs):
        cls.__interface_info__ = interface_info_from_xml(cls.__introspection_xml__)

        gproperties = {
            property_info.name: (
                unpack_type(property_info.signature).pytype,
                property_info.name,
                f'{property_info.name} D-Bus property',
                *(unpack_type(property_info.signature).pspec),
                GObject.ParamFlags.EXPLICIT_NOTIFY | (property_info.flags & GObject.ParamFlags.READABLE)
            ) for property_info in cls.__interface_info__.properties
        }

        gproperties.update(getattr(cls, '__gproperties__', {}))
        cls.__gproperties__ = gproperties

        gsignals = {
            signal_info.name: (
                GObject.SIGNAL_RUN_FIRST,
                None,
                tuple(unpack_type(arg.signature).pytype for arg in signal_info.args)
            ) for signal_info in cls.__interface_info__.signals
        }

        gsignals.update(getattr(cls, '__gsignals__', {}))
        cls.__gsignals__ = gsignals

        for method_info in cls.__interface_info__.methods:
            setattr(cls, method_info.name, DBusMethod(method_info))

        return super().__init_subclass__(**kwargs)

    def __init__(self, **kwargs):
        kwargs.setdefault('g_interface_info', self.__class__.__interface_info__)
        kwargs.setdefault('g_interface_name', self.__class__.__interface_info__.name)
        kwargs.setdefault('g_flags', Gio.DBusProxyFlags.GET_INVALIDATED_PROPERTIES)
        super().__init__(**kwargs)

    def do_get_property(self, prop):
        if self.__class__.__interface_info__.lookup_property(prop.name):
            return unpack(self.get_cached_property(prop.name))

        return super().do_get_property(prop)

    def do_g_properties_changed(self, changed_properties, _):
        for prop in changed_properties.keys():
            if self.__class__.__interface_info__.lookup_property(prop):
                self.notify(prop)

    def do_g_signal(self, _, signal_name, parameters):
        if self.__class__.__interface_info__.lookup_signal(signal_name):
            self.emit(signal_name, *unpack_tuple(parameters))

    def __getattr__(self):
        raise AttributeError
