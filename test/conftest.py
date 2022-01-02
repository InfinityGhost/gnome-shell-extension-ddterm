import atexit
import collections
import contextlib
import functools
import json
import logging
import pathlib
import shlex
import subprocess
import sys
import threading

import pytest

from gi.repository import Gio

from . import dbus_util


LOGGER = logging.getLogger(__name__)

TEST_SRC_DIR = pathlib.Path(__file__).parent.resolve()
SRC_DIR = TEST_SRC_DIR.parent
EXTENSION_UUID = 'ddterm@amezin.github.com'
PKG_PATH = f'/home/gnomeshell/.local/share/gnome-shell/extensions/{EXTENSION_UUID}'

IMAGES = [
    'ghcr.io/amezin/gnome-shell-pod-32:master',
    'ghcr.io/amezin/gnome-shell-pod-33:master',
    'ghcr.io/amezin/gnome-shell-pod-34:master',
    'ghcr.io/amezin/gnome-shell-pod-35:master',
]

DUAL_MONITOR_SESSION = 'gnome-wayland-nested-dual-monitor'
SESSIONS = [
    'gnome-xsession',
    'gnome-wayland-nested',
    'gnome-wayland-nested-highdpi',
    DUAL_MONITOR_SESSION
]

MonitorConfig = collections.namedtuple('MonitorConfig', ['current_index', 'setting'])

SINGLE_MONITOR_CONFIGS = [MonitorConfig(0, 'current')]
DUAL_MONITOR_CONFIGS = [
    MonitorConfig(0, 'current'),
    MonitorConfig(1, 'current'),
    MonitorConfig(1, 'primary')
]

current_container = None


@pytest.fixture(scope='session')
def container_image(request, pytestconfig, podman):
    if pytestconfig.option.pull:
        podman('pull', request.param, timeout=None)

    return request.param


@pytest.fixture(scope='session')
def gnome_shell_session_name(request):
    return request.param


@pytest.fixture(scope='session')
def monitor_config(request, container_image, gnome_shell_session_name):
    # Depends on other fixtures to have lowest grouping/ordering priority
    return request.param


def pytest_addoption(parser):
    parser.addoption('--container-image', action='append')
    parser.addoption('--gnome-session', action='append')
    parser.addoption('--podman', default=['podman'], nargs='+')
    parser.addoption('--pull', default=False, action='store_true')


def pytest_generate_tests(metafunc):
    if 'container_image' in metafunc.fixturenames:
        images = metafunc.config.getoption('--container-image')
        if not images:
            images = IMAGES

        metafunc.parametrize('container_image', images, indirect=True, scope='session')

    if 'gnome_shell_session_name' not in metafunc.fixturenames:
        return

    sessions = metafunc.config.getoption('--gnome-session')
    if not sessions:
        sessions = SESSIONS

    if 'monitor_config' not in metafunc.fixturenames:
        metafunc.parametrize('gnome_shell_session_name', sessions, indirect=True, scope='session')
        return

    params = []
    ids = []
    for session in sessions:
        configs = DUAL_MONITOR_CONFIGS if session == DUAL_MONITOR_SESSION else SINGLE_MONITOR_CONFIGS

        for config in configs:
            params.append((session, config))
            ids.append(f'{session}-mousemon{config.current_index}-target{config.setting}')

    metafunc.parametrize(('gnome_shell_session_name', 'monitor_config'), params, ids=ids, indirect=True, scope='session')


@pytest.fixture(scope='session')
def podman_cmd(pytestconfig):
    base = tuple(pytestconfig.option.podman)

    def gen(*args):
        return base + args

    return gen


@pytest.fixture(scope='session')
def podman(podman_cmd):
    def run(*args, **kwargs):
        kwargs.setdefault('check', True)
        kwargs.setdefault('timeout', 30)
        cmd = podman_cmd(*args)
        cmd_str = shlex.join(cmd)
        LOGGER.info('Running: %s', cmd_str)
        proc = subprocess.run(cmd, **kwargs)
        LOGGER.info('Done: %s', cmd_str)
        return proc

    return run


@pytest.fixture(scope='session')
def xvfb_fbdir(tmpdir_factory):
    return tmpdir_factory.mktemp('xvfb')


class Container:
    def __init__(self, podman, container_id):
        self.container_id = container_id
        self.podman = podman
        self.exec_args = ['exec', '--user', 'gnomeshell', self.container_id, 'set-env.sh']
        self.journal_sync_event = threading.Event()
        self.journal_sync_token = None
        self.journal_sync_lock = threading.Lock()
        self.systemd_cat = None

    def exec(self, *args, **kwargs):
        return self.podman(*self.exec_args, *args, **kwargs)

    def journal_sync(self, token):
        token = token.encode()

        with self.journal_sync_lock:
            self.journal_sync_event.clear()
            self.journal_sync_token = token

        try:
            self.restart_systemd_cat()
            self.systemd_cat.stdin.write(token + b'\n')
            self.journal_sync_event.wait(timeout=1)
        except Exception:
            LOGGER.exception("Can't sync journal")

    def restart_systemd_cat(self):
        res = self.systemd_cat.poll()
        if res is not None:
            LOGGER.error('systemd-cat exited with %r, restarting...', res)
            self.systemd_cat = subprocess.Popen(
                self.systemd_cat.args, stdin=subprocess.PIPE, bufsize=0
            )

    @contextlib.contextmanager
    def journal_context(self, name):
        self.restart_systemd_cat()
        self.systemd_cat.stdin.write(f'Beginning of {name}\n'.encode())
        try:
            yield
        finally:
            self.journal_sync(f'End of {name}')


@pytest.fixture(scope='session')
def container(podman, podman_cmd, container_image, gnome_shell_session_name, pytestconfig, xvfb_fbdir):
    container_id = podman(
        'run', '--rm', '-Ptd', '--cap-add', 'SYS_NICE', '--cap-add', 'IPC_LOCK',
        '-v', f'{SRC_DIR}:{PKG_PATH}:ro',
        '-v', f'{TEST_SRC_DIR}/fbdir.conf:/etc/systemd/system/xvfb@.service.d/fbdir.conf:ro',
        '-v', f'{TEST_SRC_DIR}/journald.conf:/etc/systemd/journald.conf:ro',
        '-v', f'{xvfb_fbdir}:/xvfb',
        container_image,
        stdout=subprocess.PIPE, text=True
    ).stdout

    if container_id.endswith('\n'):
        container_id = container_id[:-1]

    global current_container
    current_container = Container(podman, container_id)

    journal_cmd = podman_cmd(
        'attach', '--no-stdin', container_id
    )
    journal = subprocess.Popen(
        journal_cmd, stderr=subprocess.STDOUT, stdout=subprocess.PIPE, bufsize=0
    )

    def read_journal():
        with journal.stdout as stream:
            current_line = bytes()

            while True:
                current_line += stream.read(4096)
                if not current_line:
                    return

                lines = current_line.splitlines(keepends=True)
                if lines[-1].endswith(b'\n'):
                    current_line = bytes()
                else:
                    current_line = lines[-1]
                    lines = lines[:-1]

                for line in lines:
                    sys.stderr.buffer.write(line)

                    with current_container.journal_sync_lock:
                        token = current_container.journal_sync_token
                        if token is not None and token in line:
                            current_container.journal_sync_event.set()

    reader = threading.Thread(target=read_journal)
    reader.start()

    systemd_cat_cmd = podman_cmd('exec', '-i', container_id, 'systemd-cat', '-p', 'notice', '--level-prefix=0')
    systemd_cat = subprocess.Popen(systemd_cat_cmd, stdin=subprocess.PIPE, bufsize=0)
    current_container.systemd_cat = systemd_cat

    def stop():
        podman('kill', container_id)
        systemd_cat.wait()
        journal.wait()
        reader.join()

    atexit.register(stop)

    try:
        yield current_container

    finally:
        stop()
        atexit.unregister(stop)


def make_journal_context(item, when):
    if current_container is None:
        return contextlib.nullcontext()

    return current_container.journal_context(f'{item.nodeid} {when}')


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_setup(item):
    with make_journal_context(item, 'setup'):
        yield


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_call(item):
    with make_journal_context(item, 'call'):
        yield


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_teardown(item):
    with make_journal_context(item, 'teardown'):
        yield


@pytest.fixture(scope='session')
def container_session_bus_ready(container):
    container.exec('wait-user-bus.sh')


@pytest.fixture(scope='session')
def gnome_shell_session(container, container_session_bus_ready, gnome_shell_session_name):
    container.exec('systemctl', '--user', 'start', f'{gnome_shell_session_name}@:99')
    return gnome_shell_session_name


@pytest.fixture(scope='session')
def bus_connection(podman, container, container_session_bus_ready):
    inspect = json.loads(podman('inspect', container.container_id, stdout=subprocess.PIPE).stdout)

    hostport = inspect[0]['NetworkSettings']['Ports']['1234/tcp'][0];
    host = hostport['HostIp'] or '127.0.0.1'
    port = hostport['HostPort']

    return Gio.DBusConnection.new_for_address_sync(
        f'tcp:host={host},port={port}',
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None,
        None
    )


@pytest.fixture(scope='session')
def bus_call(bus_connection):
    return functools.partial(dbus_util.call, bus_connection)


@pytest.fixture(scope='session')
def bus_get_property(bus_connection):
    return functools.partial(dbus_util.get_property, bus_connection)


@pytest.fixture(scope='session')
def shell_extensions_interface_ready(bus_connection, gnome_shell_session):
    dbus_util.wait_interface(bus_connection, path='/org/gnome/Shell', interface='org.gnome.Shell.Extensions')


@pytest.fixture(scope='session')
def extension_enabled(bus_call, shell_extensions_interface_ready):
    bus_call('EnableExtension', '(s)', EXTENSION_UUID, path='/org/gnome/Shell', interface='org.gnome.Shell.Extensions')
