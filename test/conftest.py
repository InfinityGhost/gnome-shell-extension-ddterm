import atexit
import functools
import io
import json
import logging
import pathlib
import shlex
import subprocess
import tarfile

import pytest
import wand.image

from . import dbus_proxy, glib_util


LOGGER = logging.getLogger(__name__)

DEFAULT_IMAGE = 'ghcr.io/amezin/gnome-shell-pod-32:master'
DEFAULT_SESSION = 'gnome-xsession'

SRC_DIR = (pathlib.Path(__file__).parent / '..').resolve()
EXTENSION_UUID = 'ddterm@amezin.github.com'
PKG_PATH = f'/home/gnomeshell/.local/share/gnome-shell/extensions/{EXTENSION_UUID}'

EXTENSION_DBUS_XML_PATH = pathlib.Path(__file__).resolve().parents[1] / 'com.github.amezin.ddterm.Extension.xml'


class ExtensionDBusProxy(dbus_proxy.DBusProxy):
    __introspection_xml__ = EXTENSION_DBUS_XML_PATH.read_text()


def pytest_addoption(parser):
    parser.addoption('--image', default=DEFAULT_IMAGE)
    parser.addoption('--gnome-session', default=DEFAULT_SESSION)
    parser.addoption('--podman', default='podman')


@pytest.fixture(scope='session')
def podman_cmd(pytestconfig):
    base = tuple(shlex.split(pytestconfig.option.podman))

    def gen(*args):
        return base + args

    return gen


@pytest.fixture(scope='session')
def podman(podman_cmd):
    def run(*args, **kwargs):
        kwargs.setdefault('check', True)
        cmd = podman_cmd(*args)
        cmd_str = shlex.join(cmd)
        LOGGER.info('Running: %s', cmd_str)
        proc = subprocess.run(cmd, **kwargs)
        LOGGER.info('Done: %s', cmd_str)
        return proc

    return run


class Container:
    def __init__(self, podman, container_id):
        self.container_id = container_id
        self.podman = podman
        self.exec_args = ['exec', '--user', 'gnomeshell', self.container_id, 'set-env.sh']

    def exec(self, *args, **kwargs):
        return self.podman(*self.exec_args, *args, **kwargs)

    def inspect(self):
        return json.loads(self.podman('inspect', self.container_id, stdout=subprocess.PIPE).stdout)


@pytest.fixture(scope='session')
def container(podman, pytestconfig):
    container_id = podman(
        'run', '--rm', '-Ptd', '--cap-add', 'SYS_NICE', '--cap-add', 'IPC_LOCK',
        '-v', f'{SRC_DIR}:{PKG_PATH}:ro', pytestconfig.option.image,
        stdout=subprocess.PIPE, text=True
    ).stdout

    if container_id.endswith('\n'):
        container_id = container_id[:-1]

    def kill():
        podman('kill', container_id)

    atexit.register(kill)

    try:
        yield Container(podman, container_id)

    finally:
        atexit.unregister(kill)
        kill()


@pytest.fixture(scope='session')
def container_session_bus_ready(container):
    container.exec('wait-user-bus.sh')


@pytest.fixture(scope='session')
def container_session_bus_address(container):
    desc = container.inspect()
    hostport = desc[0]['NetworkSettings']['Ports']['1234/tcp'][0];
    host = hostport['HostIp'] or '127.0.0.1'
    port = hostport['HostPort']

    return f'tcp:host={host},port={port}'


@pytest.fixture(scope='session')
def container_session_bus_connection(container_session_bus_address, container_session_bus_ready):
    bus = glib_util.wait_dbus_connection(container_session_bus_address, timeout_ms=1000)

    try:
        yield bus

    finally:
        bus.close_sync(None)


@pytest.fixture(scope='session')
def gnome_shell_session(container, container_session_bus_ready, pytestconfig):
    session = pytestconfig.option.gnome_session
    container.exec('systemctl', '--user', 'start', f'{session}@:99')
    return session


@pytest.fixture(scope='session')
def session_dbus_interface(container_session_bus_connection):
    return functools.partial(
        glib_util.wait_dbus_interface,
        container_session_bus_connection,
        timeout_ms=10000
    )


@pytest.fixture(scope='session')
def shell_dbus_interface(session_dbus_interface, gnome_shell_session):
    return session_dbus_interface(
        dest='org.gnome.Shell',
        path='/org/gnome/Shell',
        interface='org.gnome.Shell'
    )


@pytest.fixture(scope='session')
def shell_extensions_dbus_interface(session_dbus_interface, gnome_shell_session):
    return session_dbus_interface(
        dest='org.gnome.Shell',
        path='/org/gnome/Shell',
        interface='org.gnome.Shell.Extensions'
    )


@pytest.fixture(scope='session')
def enable_extension(shell_extensions_dbus_interface):
    success = shell_extensions_dbus_interface.EnableExtension('(s)', EXTENSION_UUID)
    assert success is True
    return EXTENSION_UUID


@pytest.fixture(scope='session')
def extension_dbus_interface(session_dbus_interface, enable_extension):
    return session_dbus_interface(
        dest='org.gnome.Shell',
        path='/org/gnome/Shell/Extensions/ddterm',
        interface='com.github.amezin.ddterm.Extension',
        proxy_class=ExtensionDBusProxy
    )


@pytest.fixture(scope='session')
def journal(podman_cmd, container, container_session_bus_ready):
    cmd = podman_cmd(*container.exec_args, 'journalctl', '-f')
    cmd_str = shlex.join(cmd)
    LOGGER.info('Starting: %s', cmd_str)
    tail = subprocess.Popen(cmd)

    try:
        yield tail

    finally:
        tail.terminate()
        tail.wait()
        LOGGER.info('Stopped %s', cmd_str)


@pytest.fixture(scope='session')
def screenshot(container, gnome_shell_session):
    try:
        yield
    finally:
        screenshot_tar = container.podman('cp', f'{container.container_id}:/run/Xvfb_screen0', '-', stdout=subprocess.PIPE).stdout
        with tarfile.open(fileobj=io.BytesIO(screenshot_tar)) as tar:
            for tarinfo in tar:
                fileobj = tar.extractfile(tarinfo)
                if not fileobj:
                    continue

                with fileobj:
                    with wand.image.Image(file=fileobj, format='xwd') as img:
                        with img.convert(format='png') as converted:
                            converted.save(filename=f'{tarinfo.name}.png')


@pytest.fixture(scope='session')
def close_welcome_dialog(shell_dbus_interface):
    shell_dbus_interface.Eval('(s)', '''
        if (global.settings.settings_schema.has_key('welcome-dialog-last-shown-version'))
            global.settings.set_string('welcome-dialog-last-shown-version', '99.0');

        if (Main.welcomeDialog) {
            const ModalDialog = imports.ui.modalDialog;
            if (Main.welcomeDialog.state !== ModalDialog.State.CLOSED)
                Main.welcomeDialog.close();
        }
    ''')
