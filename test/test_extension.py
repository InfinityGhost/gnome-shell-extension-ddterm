import base64
import collections
import contextlib
import itertools
import pathlib

import pytest
import wand.image
from pytest_html import extras

from . import dbus_util


@pytest.fixture
def screenshot(container, gnome_shell_session, extra, xvfb_fbdir, tmp_path):
    @contextlib.contextmanager
    def do_screenshot():
        try:
            yield
        finally:
            xwd_blob = pathlib.Path(xvfb_fbdir / 'Xvfb_screen0').read_bytes()

            with wand.image.Image(blob=xwd_blob, format='xwd') as img:
                png_blob = img.make_blob('png')

            extra.append(extras.png(base64.b64encode(png_blob).decode('ascii')))

    return do_screenshot


@pytest.fixture(scope='session', autouse=True)
def extension_test_interface_ready(bus_connection, extension_enabled):
    dbus_util.wait_interface(bus_connection)


@pytest.fixture(scope='session', autouse=True)
def extension_setup(bus_call, extension_test_interface_ready):
    bus_call('Setup')


@pytest.fixture(scope='session', autouse=True)
def n_monitors(bus_get_property, extension_setup, gnome_shell_session_name):
    return bus_get_property('NMonitors')


@pytest.fixture(scope='session', autouse=True)
def primary_monitor(bus_get_property, extension_setup, gnome_shell_session_name):
    return bus_get_property('PrimaryMonitor')


@pytest.fixture(scope='session', autouse=True)
def verify_config(primary_monitor, n_monitors, gnome_shell_session_name):
    assert primary_monitor == 0

    if gnome_shell_session_name == 'gnome-wayland-nested-dual-monitor':
        assert n_monitors == 2
    else:
        assert n_monitors == 1


Rect = collections.namedtuple('Rect', ('x', 'y', 'width', 'height'))


@pytest.fixture(scope='session')
def monitors_geometry(bus_call, n_monitors):
    return [Rect(*bus_call('GetMonitorGeometry', '(i)', index, return_type='(iiii)')) for index in range(n_monitors)]


@pytest.fixture(scope='session')
def monitors_scale(bus_call, n_monitors):
    return [bus_call('GetMonitorScale', '(i)', index, return_type='(i)')[0] for index in range(n_monitors)]


@pytest.fixture(scope='session')
def shell_version(bus_get_property, shell_extensions_interface_ready):
    return bus_get_property(
        'ShellVersion',
        path='/org/gnome/Shell',
        interface='org.gnome.Shell'
    )


MAXIMIZE_MODES = ['not-maximized', 'maximize-early', 'maximize-late']
HORIZONTAL_RESIZE_POSITIONS = ['left', 'right']
VERTICAL_RESIZE_POSITIONS = ['top', 'bottom']
POSITIONS = VERTICAL_RESIZE_POSITIONS + HORIZONTAL_RESIZE_POSITIONS
SIZE_VALUES = [0.5, 0.9, 1.0]


@pytest.mark.parametrize('window_size', [0.31, 0.36, 0.4, 0.8, 0.85, 0.91])
@pytest.mark.parametrize('window_maximize', MAXIMIZE_MODES)
@pytest.mark.parametrize('window_pos', VERTICAL_RESIZE_POSITIONS)
def test_show_v(bus_call, window_size, window_maximize, window_pos, monitor_config, screenshot):
    with screenshot():
        bus_call('TestShow', '(dssis)', window_size, window_maximize, window_pos, monitor_config.current_index, monitor_config.setting)


@pytest.mark.parametrize('window_size', [0.31, 0.36, 0.4, 0.8, 0.85, 0.91])
@pytest.mark.parametrize('window_maximize', MAXIMIZE_MODES)
@pytest.mark.parametrize('window_pos', HORIZONTAL_RESIZE_POSITIONS)
def test_show_h(bus_call, window_size, window_maximize, window_pos, monitor_config, primary_monitor, monitors_geometry, monitors_scale, screenshot):
    if monitor_config.setting == 'primary':
        target_monitor = primary_monitor
    else:
        target_monitor = monitor_config.current_index

    if monitors_geometry[target_monitor].width * window_size < 472 * monitors_scale[target_monitor]:
        pytest.skip('Screen too small')

    with screenshot():
        bus_call('TestShow', '(dssis)', window_size, window_maximize, window_pos, monitor_config.current_index, monitor_config.setting)


#@pytest.mark.parametrize('window_size', SIZE_VALUES)
#@pytest.mark.parametrize('window_maximize', MAXIMIZE_MODES)
#@pytest.mark.parametrize('window_size2', SIZE_VALUES)
#@pytest.mark.parametrize('window_pos', POSITIONS)
#def test_resize_xte(bus_call, window_size, window_maximize, window_size2, window_pos, monitor_config, shell_version, screenshot):
#    with screenshot():
#        version_split = tuple(int(x) for x in shell_version.split('.'))
#        if version_split < (3, 38):
#            if monitor_config.current_index == 1 and window_pos == 'bottom' and window_size2 == 1:
#                pytest.skip('For unknown reason it fails to resize to full height on 2nd monitor')

#        bus_call('TestResizeXte', '(dsdsis)', window_size, window_maximize, window_size2, window_pos, monitor_config.current_index, monitor_config.setting)


#@pytest.mark.parametrize('window_size', SIZE_VALUES)
#@pytest.mark.parametrize(('window_pos', 'window_pos2'), (p for p in itertools.product(POSITIONS, repeat=2) if p[0] != p[1]))
#def test_change_position(bus_call, window_size, window_pos, window_pos2, monitor_config, screenshot):
#    with screenshot():
#        bus_call('TestChangePosition', '(dssis)', window_size, window_pos, window_pos2, monitor_config.current_index, monitor_config.setting)


#@pytest.mark.parametrize('window_size', SIZE_VALUES)
#@pytest.mark.parametrize('window_maximize', MAXIMIZE_MODES)
#@pytest.mark.parametrize('window_pos', POSITIONS)
#def test_unmaximize(bus_call, window_size, window_maximize, window_pos, monitor_config, screenshot):
#    with screenshot():
#        bus_call('TestUnmaximize', '(dssis)', window_size, window_maximize, window_pos, monitor_config.current_index, monitor_config.setting)


#@pytest.mark.parametrize('window_size', SIZE_VALUES)
#@pytest.mark.parametrize('window_size2', SIZE_VALUES)
#@pytest.mark.parametrize('window_pos', POSITIONS)
#def test_unmaximize_correct_size(bus_call, window_size, window_size2, window_pos, monitor_config, screenshot):
#    with screenshot():
#        bus_call('TestUnmaximizeCorrectSize', '(ddsis)', window_size, window_size2, window_pos, monitor_config.current_index, monitor_config.setting)


#@pytest.mark.parametrize(('window_size', 'window_size2'), (p for p in itertools.product(SIZE_VALUES, repeat=2) if p[0] != p[1]))
#@pytest.mark.parametrize('window_pos', POSITIONS)
#def test_unmaximize_on_size_change(bus_call, window_size, window_size2, window_pos, monitor_config, screenshot):
#    with screenshot():
#        bus_call('TestUnmaximizeOnSizeChange', '(ddsis)', window_size, window_size2, window_pos, monitor_config.current_index, monitor_config.setting)
