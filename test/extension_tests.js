/*
    Copyright © 2021 Aleksandr Mezin

    This file is part of ddterm GNOME Shell extension.

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
*/

'use strict';

/* exported enable disable message debug info warning critical error */

const { GLib, GObject, Gio, Meta } = imports.gi;
const ByteArray = imports.byteArray;
const Main = imports.ui.main;
const JsUnit = imports.jsUnit;
const Me = imports.misc.extensionUtils.getCurrentExtension();
const Extension = Me.imports.extension;
const { ConnectionSet } = Me.imports.connectionset;

const WindowMaximizeMode = {
    NOT_MAXIMIZED: 'not-maximized',
    EARLY: 'maximize-early',
    LATE: 'maximize-late',
};

let settings = null;
const window_trace = new ConnectionSet();

const CURSOR_TRACKER_MOVED_SIGNAL = GObject.signal_lookup('cursor-moved', Meta.CursorTracker) ? 'cursor-moved' : 'position-invalidated';
const CURSOR_TRACKER = Meta.CursorTracker.get_for_display(global.display);
const WAIT_TIMEOUT_MS = 2000;
const START_TIMEOUT_MS = 10000;

const LOG_DOMAIN = 'ddterm-test';

function _makeLogFunction(level) {
    return message => {
        let stack = new Error().stack;
        let caller = stack.split('\n')[1];

        let [code, line] = caller.split(':');
        let [func, file] = code.split(/\W*@/);

        GLib.log_structured(LOG_DOMAIN, level, {
            'MESSAGE': `[${func}:${line}] ${message}`,
            'SYSLOG_IDENTIFIER': 'ddterm.ExtensionTest',
            'CODE_FILE': file,
            'CODE_FUNC': func,
            'CODE_LINE': line,
        });
    };
}

const message = _makeLogFunction(GLib.LogLevelFlags.LEVEL_MESSAGE);
const debug = _makeLogFunction(GLib.LogLevelFlags.LEVEL_DEBUG);
const info = _makeLogFunction(GLib.LogLevelFlags.LEVEL_INFO);
const warning = _makeLogFunction(GLib.LogLevelFlags.LEVEL_WARNING);
const critical = _makeLogFunction(GLib.LogLevelFlags.LEVEL_CRITICAL);
const error = _makeLogFunction(GLib.LogLevelFlags.LEVEL_ERROR);

function invoke_async(f, params, invocation) {
    f(...params).then(_ => {
        invocation.return_value(null);
    }).catch(e => {
        if (e instanceof GLib.Error) {
            invocation.return_gerror(e);
        } else {
            let name = e.name;
            if (!name.includes('.')) {
                // likely to be a normal JS error
                name = `org.gnome.gjs.JSError.${name}`;
            }
            logError(e, `Exception in method call: ${invocation.get_method_name()}`);
            invocation.return_dbus_error(name, `${e}\n\n${e.stack}`);
        }
    });
}

async function invoke_test(f, ...params) {
    const handlers = new ConnectionSet();
    handlers.connect(Extension.window_manager, 'notify::current-window', setup_window_trace);
    handlers.connect(Extension.window_manager, 'move-resize-requested', (_, rect) => {
        info(`Extension requested move-resize to { .x = ${rect.x}, .y = ${rect.y}, .width = ${rect.width}, .height = ${rect.height} }`);
    });
    try {
        await f(...params);
    } finally {
        handlers.disconnect();
    }
}

function invoke_test_async(f, params, invocation) {
    invoke_async(invoke_test.bind(null, f), params, invocation);
}

async function setup() {
    if (global.settings.settings_schema.has_key('welcome-dialog-last-shown-version'))
        global.settings.set_string('welcome-dialog-last-shown-version', '99.0');

    if (Main.layoutManager._startingUp) {
        message('Waiting for startup to complete');
        await async_wait_signal(
            Main.layoutManager,
            'startup-complete',
            () => !Main.layoutManager._startingUp,
            START_TIMEOUT_MS
        );
        message('Startup complete');
    }

    imports.gi.St.Settings.get().inhibit_animations();

    if (Main.welcomeDialog) {
        const ModalDialog = imports.ui.modalDialog;
        if (Main.welcomeDialog.state !== ModalDialog.State.CLOSED) {
            message('Closing welcome dialog');
            const wait_close = async_wait_signal(
                Main.welcomeDialog,
                'closed',
                () => Main.welcomeDialog.state === ModalDialog.State.CLOSED
            );
            Main.welcomeDialog.close();
            await wait_close;
            message('Welcome dialog closed');
        }
    }

    if (Main.overview.visible) {
        message('Hiding overview');
        const wait_hide = async_wait_signal(
            Main.overview,
            'hidden',
            () => !Main.overview.visible
        );
        Main.overview.hide();
        await wait_hide;
        message('Overview hidden');
    }

    message('Starting the app/showing window');
    await async_show_window(START_TIMEOUT_MS);
    message('Window shown');

    await async_idle();
}

function setup_window_trace() {
    const win = Extension.window_manager.current_window;

    info(`current window changed: ${win}`);

    window_trace.disconnect();

    if (!win)
        return;

    window_trace.connect(win, 'position-changed', () => {
        const rect = win.get_frame_rect();
        info(`position-changed: { .x = ${rect.x}, .y = ${rect.y}, .width = ${rect.width}, .height = ${rect.height} }`);
    });

    window_trace.connect(win, 'size-changed', () => {
        const rect = win.get_frame_rect();
        info(`size-changed: { .x = ${rect.x}, .y = ${rect.y}, .width = ${rect.width}, .height = ${rect.height} }`);
    });

    window_trace.connect(win, 'notify::maximized-vertically', () => {
        info(`notify::maximized-vertically = ${win.maximized_vertically}`);
    });

    window_trace.connect(win, 'notify::maximized-horizontally', () => {
        info(`notify::maximized-horizontally = ${win.maximized_horizontally}`);
    });
}

function async_sleep(ms) {
    return new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_LOW, ms, () => {
        resolve();
        return GLib.SOURCE_REMOVE;
    }));
}

function async_idle() {
    return new Promise(resolve => Meta.later_add(Meta.LaterType.IDLE, () => {
        resolve();
        return GLib.SOURCE_REMOVE;
    }));
}

function with_timeout(promise, timeout_ms = WAIT_TIMEOUT_MS) {
    return Promise.race([
        promise,
        new Promise((resolve, reject) => async_sleep(timeout_ms).then(() => {
            reject(new Error('Timed out'));
        })),
    ]);
}

function async_wait_frame() {
    return with_timeout(new Promise(resolve => {
        const window = Extension.window_manager.current_window;
        if (!window) {
            resolve();
            return;
        }

        const actor = window.get_compositor_private();
        if (!actor) {
            resolve();
            return;
        }

        message('Waiting for next frame');

        const stage = global.get_stage();
        if (actor.peek_stage_views) {
            const handler = stage.connect('after-paint', (_, view) => {
                const views = actor.peek_stage_views();
                if (views.includes(view)) {
                    stage.disconnect(handler);
                    message('Frame painted');
                    async_idle().then(resolve);
                } else {
                    message(`Paint happened, but for wrong view: ${view}`);
                }
            });
        } else {
            async_wait_signal(stage, 'after-paint').then(() => {
                message('Frame painted');
                async_idle().then(resolve);
            });
        }
    }));
}

function wait_frame_wayland() {
    if (Meta.is_wayland_compositor())
        return async_wait_frame();
    else
        return async_idle();
}

function hide_window_async_wait() {
    return with_timeout(new Promise(resolve => {
        if (!Extension.window_manager.current_window) {
            resolve();
            return;
        }

        message('Hiding the window');

        const wm = Extension.window_manager;
        async_wait_signal(
            wm,
            'notify::current-window',
            () => wm.current_window === null
        ).then(() => {
            message('Window hidden');
            return wait_frame_wayland();
        }).then(resolve);

        Extension.toggle();
    }));
}

function async_show_window(timeout_ms = WAIT_TIMEOUT_MS) {
    return with_timeout(new Promise(resolve => {
        message('Waiting for the window to show');

        const wm = Extension.window_manager;
        JsUnit.assertNull(wm.current_window);

        async_wait_signal(
            wm,
            'notify::current-window',
            () => wm.current_window !== null,
            timeout_ms
        ).then(() => {
            const win = wm.current_window;
            message(`Current window: ${win}`);
            const actor = win.get_compositor_private();
            return async_wait_signal(
                actor,
                'notify::mapped',
                () => actor.mapped,
                timeout_ms
            );
        }).then(() => {
            message('Window actor mapped');
            return wait_frame_wayland();
        }).then(wait_frame_wayland).then(resolve);

        Extension.toggle();
    }), timeout_ms);
}

function async_wait_signal(object, signal, predicate = null, timeout_ms = WAIT_TIMEOUT_MS, recheck_interval = null) {
    return with_timeout(new Promise(resolve => {
        const pred_check = () => {
            if (!predicate()) {
                if (recheck_interval !== null)
                    async_sleep(recheck_interval).then(pred_check);

                return;
            }

            object.disconnect(handler_id);
            resolve();
        };

        const handler_id = object.connect(signal, pred_check);
        if (predicate)
            pred_check();
        else
            predicate = () => true;

        if (recheck_interval !== null)
            async_sleep(recheck_interval).then(pred_check);
    }), timeout_ms);
}

function async_run_process(argv) {
    return with_timeout(new Promise(resolve => {
        info(`Starting subprocess ${JSON.stringify(argv)}`);
        const subprocess = Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE);
        subprocess.wait_check_async(null, (source, result) => {
            info(`Finished subprocess ${JSON.stringify(argv)}`);
            resolve(source.wait_check_finish(result));
        });
    }));
}

function set_settings_double(name, value) {
    info(`Setting ${name}=${value}`);
    return settings.set_double(name, value);
}

function set_settings_boolean(name, value) {
    info(`Setting ${name}=${value}`);
    return settings.set_boolean(name, value);
}

function set_settings_string(name, value) {
    info(`Setting ${name}=${value}`);
    return settings.set_string(name, value);
}

function assert_rect_equals(expected, actual) {
    message(`Checking if rect { .x=${actual.x}, .y=${actual.y}, .width=${actual.width}, .height=${actual.height} } matches expected { .x=${expected.x}, .y=${expected.y}, .width=${expected.width}, .height=${expected.height} }`);
    JsUnit.assertEquals(expected.x, actual.x);
    JsUnit.assertEquals(expected.y, actual.y);
    JsUnit.assertEquals(expected.width, actual.width);
    JsUnit.assertEquals(expected.height, actual.height);
}

function verify_window_geometry(window_size, window_maximize, window_pos, monitor_index) {
    const workarea = Main.layoutManager.getWorkAreaForMonitor(monitor_index);
    const monitor_scale = global.display.get_monitor_scale(monitor_index);
    const frame_rect = Extension.window_manager.current_window.get_frame_rect();

    message(`Verifying window geometry (expected size=${window_size}, maximized=${window_maximize}, position=${window_pos})`);

    if (window_pos === 'top' || window_pos === 'bottom') {
        JsUnit.assertEquals(window_maximize, Extension.window_manager.current_window.maximized_vertically);
        JsUnit.assertEquals(Extension.window_manager.current_window.maximized_vertically, settings.get_boolean('window-maximize'));
    } else {
        JsUnit.assertEquals(window_maximize, Extension.window_manager.current_window.maximized_horizontally);
        JsUnit.assertEquals(Extension.window_manager.current_window.maximized_horizontally, settings.get_boolean('window-maximize'));
    }

    if (window_maximize) {
        assert_rect_equals(workarea, frame_rect);
        return;
    }

    const target_rect = Extension.window_manager.target_rect_for_workarea_size(workarea, monitor_scale, window_size);
    assert_rect_equals(target_rect, Extension.window_manager.current_target_rect);

    const workarea_right = workarea.x + workarea.width;
    const workarea_bottom = workarea.y + workarea.height;
    const frame_rect_right = frame_rect.x + frame_rect.width;
    const frame_rect_bottom = frame_rect.y + frame_rect.height;

    if (window_pos === 'top') {
        debug('Making sure the window is attached to top edge');
        JsUnit.assertEquals(workarea.x, frame_rect.x);
        JsUnit.assertEquals(workarea_right, frame_rect_right);
        JsUnit.assertEquals(workarea.y, frame_rect.y);
    }

    if (window_pos === 'bottom') {
        debug('Making sure the window is attached to bottom edge');
        JsUnit.assertEquals(workarea.x, frame_rect.x);
        JsUnit.assertEquals(workarea_right, frame_rect_right);
        JsUnit.assertEquals(workarea_bottom, frame_rect_bottom);
    }

    if (window_pos === 'left') {
        debug('Making sure the window is attached to left edge');
        JsUnit.assertEquals(workarea.x, frame_rect.x);
        JsUnit.assertEquals(workarea.y, frame_rect.y);
        JsUnit.assertEquals(workarea_bottom, frame_rect_bottom);
    }

    if (window_pos === 'right') {
        debug('Making sure the window is attached to right edge');
        JsUnit.assertEquals(workarea_right, frame_rect_right);
        JsUnit.assertEquals(workarea.y, frame_rect.y);
        JsUnit.assertEquals(workarea_bottom, frame_rect_bottom);
    }

    assert_rect_equals(target_rect, frame_rect);

    message('Window geometry is fine');
}

function window_monitor_index(window_monitor) {
    if (window_monitor === 'current')
        return global.display.get_current_monitor();

    return Main.layoutManager.primaryIndex;
}

async function maximize_window(window_pos, window_size, value = true) {
    const window = Extension.window_manager.current_window;
    const prev = ['top', 'bottom'].includes(window_pos) ? window.maximized_vertically : window.maximized_horizontally;

    const wait = prev === value ? null : async_wait_signal(window, 'size-changed');

    set_settings_boolean('window-maximize', value);

    if (wait) {
        debug('[un]maximize: waiting for window size change');
        await wait;
        debug('[un]maximize: got window size change');
        await wait_frame_wayland();
    }
}

async function test_show(window_size, window_maximize, window_pos, current_monitor, window_monitor) {
    message(`Starting test with window size=${window_size}, maximize=${window_maximize}, position=${window_pos}`);

    if (Extension.window_manager.current_window)
        await hide_window_async_wait();

    if (current_monitor !== global.display.get_current_monitor()) {
        const monitor_rect = Main.layoutManager.monitors[current_monitor];
        await async_run_process(['xte', `mousemove ${monitor_rect.x + Math.floor(monitor_rect.width / 2)} ${monitor_rect.y + Math.floor(monitor_rect.height / 2)}`]);

        message(`Waiting for current monitor = ${current_monitor}`);
        const check = () => {
            // 'current' monitor doesn't seem to be updated in nested mode
            Meta.MonitorManager.get().emit('monitors-changed-internal');
            return current_monitor === global.display.get_current_monitor();
        };
        await async_wait_signal(
            CURSOR_TRACKER,
            CURSOR_TRACKER_MOVED_SIGNAL,
            check,
            WAIT_TIMEOUT_MS,
            25
        );
        await async_idle();
    }

    JsUnit.assertEquals(current_monitor, global.display.get_current_monitor());

    set_settings_double('window-size', window_size);
    set_settings_boolean('window-maximize', window_maximize === WindowMaximizeMode.EARLY);
    set_settings_string('window-position', window_pos);
    set_settings_string('window-monitor', window_monitor);

    await async_show_window();

    const monitor_index = window_monitor_index(window_monitor);
    const should_maximize = window_maximize === WindowMaximizeMode.EARLY || (window_size === 1.0 && settings.get_boolean('window-maximize'));
    verify_window_geometry(window_size, should_maximize, window_pos, monitor_index);

    if (window_maximize === WindowMaximizeMode.LATE) {
        await maximize_window(window_pos, window_size);
        verify_window_geometry(window_size, true, window_pos, monitor_index);
    }
}

async function test_unmaximize(window_size, window_maximize, window_pos, current_monitor, window_monitor) {
    await test_show(window_size, window_maximize, window_pos, current_monitor, window_monitor);

    const monitor_index = window_monitor_index(window_monitor);

    await maximize_window(window_pos, window_size, false);
    verify_window_geometry(window_size, false, window_pos, monitor_index);
}

async function test_unmaximize_correct_size(window_size, window_size2, window_pos, current_monitor, window_monitor) {
    await test_show(window_size, WindowMaximizeMode.NOT_MAXIMIZED, window_pos, current_monitor, window_monitor);
    const initially_maximized = settings.get_boolean('window-maximize');

    const monitor_index = window_monitor_index(window_monitor);

    set_settings_double('window-size', window_size2);
    await async_wait_frame();
    verify_window_geometry(window_size2, window_size === 1.0 && window_size2 === 1.0 && initially_maximized, window_pos, monitor_index);

    await maximize_window(window_pos, window_size2, true);
    verify_window_geometry(window_size2, true, window_pos, monitor_index);

    await maximize_window(window_pos, window_size2, false);
    verify_window_geometry(window_size2, false, window_pos, monitor_index);
}

async function test_unmaximize_on_size_change(window_size, window_size2, window_pos, current_monitor, window_monitor) {
    await test_show(window_size, WindowMaximizeMode.EARLY, window_pos, current_monitor, window_monitor);

    const monitor_index = window_monitor_index(window_monitor);

    set_settings_double('window-size', window_size2);
    await async_wait_signal(Extension.window_manager.current_window, 'size-changed');
    await async_wait_frame();

    verify_window_geometry(window_size2, window_size2 === 1.0, window_pos, monitor_index);
}

function resize_point(frame_rect, window_pos, monitor_scale) {
    let x = frame_rect.x, y = frame_rect.y;
    const edge_offset = 3 * monitor_scale;

    if (window_pos === 'left' || window_pos === 'right') {
        y += Math.floor(frame_rect.height / 2);

        if (window_pos === 'left')
            x += frame_rect.width - edge_offset;
        else
            x += edge_offset;
    } else {
        x += Math.floor(frame_rect.width / 2);

        if (window_pos === 'top')
            y += frame_rect.height - edge_offset;
        else
            y += edge_offset;
    }

    return { x, y };
}

function wait_mouse_at(x, y, mods = null, timeout_ms = WAIT_TIMEOUT_MS) {
    return with_timeout(async () => {
        const check = () => {
            const [c_x, c_y, c_mods] = global.get_pointer();
            debug(`Mouse at x: ${c_x} y: ${c_y}, modifiers: ${c_mods}`);
            return (x === c_x && y === c_y) && (mods === null || c_mods === mods);
        };

        message(`Waiting for mouse to move to x: ${x} y: ${y}`);
        await async_wait_signal(
            CURSOR_TRACKER,
            CURSOR_TRACKER_MOVED_SIGNAL,
            check,
            WAIT_TIMEOUT_MS,
            25
        );

        await async_idle();
    }, timeout_ms);
}

async function test_resize_xte(window_size, window_maximize, window_size2, window_pos, current_monitor, window_monitor) {
    await test_show(window_size, window_maximize, window_pos, current_monitor, window_monitor);

    const monitor_index = window_monitor_index(window_monitor);
    const workarea = Main.layoutManager.getWorkAreaForMonitor(monitor_index);
    const monitor_scale = global.display.get_monitor_scale(monitor_index);
    const window = Extension.window_manager.current_window;
    const initial_frame_rect = window.get_frame_rect();
    const initial = resize_point(initial_frame_rect, window_pos, monitor_scale);

    const target_frame_rect = Extension.window_manager.target_rect_for_workarea_size(workarea, monitor_scale, window_size2);
    const target = resize_point(target_frame_rect, window_pos, monitor_scale);

    const maximized_prop = ['top', 'bottom'].includes(window_pos) ? 'maximized-vertically' : 'maximized-horizontally';
    const unmaximize_wait = async_wait_signal(
        window,
        `notify::${maximized_prop}`,
        () => !window[maximized_prop]
    );
    const resize_wait = async_wait_signal(window, 'size-changed');

    await async_run_process(['xte', `mousemove ${initial.x} ${initial.y}`, 'mousedown 1']);
    await wait_mouse_at(initial.x, initial.y, 256);
    await unmaximize_wait;
    await resize_wait;
    await wait_frame_wayland();

    try {
        verify_window_geometry(window_maximize !== WindowMaximizeMode.NOT_MAXIMIZED ? 1.0 : window_size, false, window_pos, monitor_index);
        if (target.x !== initial.x || target.y !== initial.y)
            await async_run_process(['xte', `mousermove ${target.x - initial.x} ${target.y - initial.y}`]);

        await wait_mouse_at(target.x, target.y, 256);
        await wait_frame_wayland();
    } finally {
        await async_run_process(['xte', 'mouseup 1']);
    }

    await wait_mouse_at(target.x, target.y, 0);

    // TODO: 'grab-op-end' isn't emitted on Wayland when simulting mouse with xte.
    // For now, just call update_size_setting_on_grab_end()
    if (Meta.is_wayland_compositor())
        Extension.window_manager.update_size_setting_on_grab_end(global.display, Extension.window_manager.current_window);

    await wait_frame_wayland();
    verify_window_geometry(window_size2, false, window_pos, monitor_index);
}

async function test_change_position(window_size, window_pos, window_pos2, current_monitor, window_monitor) {
    await test_show(window_size, false, window_pos, current_monitor, window_monitor);
    const initially_maximized = settings.get_boolean('window-maximize');

    const monitor_index = window_monitor_index(window_monitor);

    set_settings_string('window-position', window_pos2);
    await async_idle();

    verify_window_geometry(window_size, window_size === 1.0 && initially_maximized, window_pos2, monitor_index);
}

class ExtensionTestDBusInterface {
    constructor() {
        let [_, xml] = Me.dir.get_child('test').get_child('com.github.amezin.ddterm.ExtensionTest.xml').load_contents(null);
        this.dbus = Gio.DBusExportedObject.wrapJSObject(ByteArray.toString(xml), this);
    }

    SetupAsync(params, invocation) {
        invoke_async(setup, params, invocation);
    }

    get NMonitors() {
        return global.display.get_n_monitors();
    }

    get PrimaryMonitor() {
        return Main.layoutManager.primaryIndex;
    }

    GetMonitorGeometry(index) {
        const rect = global.display.get_monitor_geometry(index);
        return [rect.x, rect.y, rect.width, rect.height];
    }

    GetMonitorScale(index) {
        return global.display.get_monitor_scale(index);
    }

    TestShowAsync(params, invocation) {
        invoke_test_async(test_show, params, invocation);
    }

    TestUnmaximizeAsync(params, invocation) {
        invoke_test_async(test_unmaximize, params, invocation);
    }

    TestUnmaximizeCorrectSizeAsync(params, invocation) {
        invoke_test_async(test_unmaximize_correct_size, params, invocation);
    }

    TestUnmaximizeOnSizeChangeAsync(params, invocation) {
        invoke_test_async(test_unmaximize_on_size_change, params, invocation);
    }

    TestResizeXteAsync(params, invocation) {
        invoke_test_async(test_resize_xte, params, invocation);
    }

    TestChangePositionAsync(params, invocation) {
        invoke_test_async(test_change_position, params, invocation);
    }
}

let dbus_interface = null;

function enable() {
    GLib.setenv('G_MESSAGES_DEBUG', LOG_DOMAIN, false);
    settings = Extension.settings;
    dbus_interface = new ExtensionTestDBusInterface();
    dbus_interface.dbus.export(Gio.DBus.session, '/org/gnome/Shell/Extensions/ddterm');
}

function disable() {
    dbus_interface.dbus.unexport();
    dbus_interface = null;
    settings = null;
}
