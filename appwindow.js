'use strict';

/* exported AppWindow */

const { GLib, GObject, Gio, Gdk, Gtk } = imports.gi;
const { util } = imports;

var AppWindow = GObject.registerClass(
    {
        Template: util.APP_DATA_DIR.get_child('appwindow.ui').get_uri(),
        Children: ['notebook', 'resize_box', 'tab_switch_button', 'new_tab_button', 'tab_switch_menu_box'],
        Properties: {
            'menus': GObject.ParamSpec.object(
                'menus', '', '', GObject.ParamFlags.READWRITE | GObject.ParamFlags.CONSTRUCT_ONLY, Gtk.Builder
            ),
            'settings': GObject.ParamSpec.object(
                'settings', '', '', GObject.ParamFlags.READWRITE | GObject.ParamFlags.CONSTRUCT_ONLY, Gio.Settings
            ),
            'desktop-settings': GObject.ParamSpec.object(
                'desktop-settings', '', '', GObject.ParamFlags.READWRITE | GObject.ParamFlags.CONSTRUCT_ONLY, Gio.Settings
            ),
        },
    },
    class AppWindow extends Gtk.ApplicationWindow {
        _init(params) {
            super._init(params);

            this.method_handler(this, 'realize', this.set_wm_functions);
            this.method_handler(this, 'screen-changed', this.setup_rgba_visual);
            this.method_handler(this, 'draw', this.draw);

            this.setup_rgba_visual();

            this.method_handler(this.settings, 'changed::background-opacity', this.update_app_paintable);
            this.update_app_paintable();

            this.method_handler(this.notebook, 'page-removed', this.close_if_no_pages);

            this.toggle_action = this.simple_action('toggle', this.toggle.bind(this));
            this.hide_action = this.simple_action('hide', () => this.hide());
            this.simple_action('new-tab', this.new_tab.bind(this));

            this.method_handler(this.resize_box, 'realize', this.set_resize_cursor);
            this.method_handler(this.resize_box, 'button-press-event', this.start_resizing);

            this.tab_select_action = new Gio.PropertyAction({
                name: 'switch-to-tab',
                object: this.notebook,
                property_name: 'page',
            });
            this.add_action(this.tab_select_action);

            this.simple_action('next-tab', () => this.notebook.next_page());
            this.simple_action('prev-tab', () => this.notebook.prev_page());

            this.bind_settings_ro('new-tab-button', this.new_tab_button, 'visible');
            this.bind_settings_ro('tab-switcher-popup', this.tab_switch_button, 'visible');

            this.method_handler(this.settings, 'changed::tab-policy', this.update_tab_bar_visibility);
            this.method_handler(this.notebook, 'page-added', this.update_tab_bar_visibility);
            this.method_handler(this.notebook, 'page-removed', this.update_tab_bar_visibility);

            this.method_handler(this.notebook, 'page-added', this.update_tab_shortcut_labels);
            this.method_handler(this.notebook, 'page-removed', this.update_tab_shortcut_labels);
            this.method_handler(this.notebook, 'page-reordered', this.update_tab_shortcut_labels);
            this.method_handler(this, 'keys-changed', this.update_tab_shortcut_labels);

            this.method_handler(this.settings, 'changed::tab-expand', this.update_tab_expand);

            this.method_handler(this.notebook, 'page-added', this.tab_switcher_add);
            this.method_handler(this.notebook, 'page-removed', this.tab_switcher_remove);
            this.method_handler(this.notebook, 'page-reordered', this.tab_switcher_reorder);

            this.method_handler(this.settings, 'changed::window-type-hint', this.update_type_hint);
            this.method_handler(this.settings, 'changed::window-skip-taskbar', this.update_type_hint);
            this.update_type_hint();

            this.new_tab();
        }

        simple_action(name, func) {
            const action = new Gio.SimpleAction({
                name,
            });
            this.signal_connect(action, 'activate', func);
            this.add_action(action);
            return action;
        }

        set_wm_functions() {
            this.window.set_functions(Gdk.WMFunction.MOVE | Gdk.WMFunction.RESIZE | Gdk.WMFunction.CLOSE);
        }

        update_tab_bar_visibility() {
            const policy = this.settings.get_string('tab-policy');
            if (policy === 'always')
                this.notebook.show_tabs = true;
            else if (policy === 'never')
                this.notebook.show_tabs = false;
            else if (policy === 'automatic')
                this.notebook.show_tabs = this.notebook.get_n_pages() > 1;
        }

        update_tab_expand() {
            for (let i = 0; i < this.notebook.get_n_pages(); i++)
                this.notebook.child_set_property(this.notebook.get_nth_page(i), 'tab-expand', this.settings.get_boolean('tab-expand'));
        }

        update_tab_shortcut_labels(_source, _child = null, start_page = 0) {
            for (let i = start_page; i < this.notebook.get_n_pages(); i++) {
                const shortcuts = this.application.get_accels_for_action(`win.switch-to-tab(${i})`);
                const shortcut = shortcuts && shortcuts.length > 0 ? shortcuts[0] : null;
                this.notebook.get_nth_page(i).switch_shortcut = shortcut;
            }
        }

        toggle() {
            if (this.visible)
                this.hide();
            else
                this.show();
        }

        new_tab() {
            const page = new imports.terminalpage.TerminalPage({
                settings: this.settings,
                menus: this.menus,
                desktop_settings: this.desktop_settings,
            });

            const index = this.notebook.append_page(page, page.tab_label);
            this.notebook.set_current_page(index);
            this.notebook.set_tab_reorderable(page, true);
            this.notebook.child_set_property(page, 'tab-expand', this.settings.get_boolean('tab-expand'));

            this.method_handler(page, 'close-request', this.remove_page);
            page.spawn();
        }

        setup_rgba_visual() {
            const visual = this.screen.get_rgba_visual();
            if (visual)
                this.set_visual(visual);
        }

        update_app_paintable() {
            this.app_paintable = this.settings.get_double('background-opacity') < 1.0;
        }

        remove_page(page) {
            this.notebook.remove(page);
            page.destroy();
        }

        close_if_no_pages() {
            if (this.notebook.get_n_pages() === 0)
                this.close();
        }

        set_resize_cursor(widget) {
            widget.window.cursor = Gdk.Cursor.new_from_name(widget.get_display(), 'ns-resize');
        }

        start_resizing(_, event) {
            const [button_ok, button] = event.get_button();
            if (!button_ok || button !== Gdk.BUTTON_PRIMARY)
                return;

            const [coords_ok, x_root, y_root] = event.get_root_coords();
            if (!coords_ok)
                return;

            this.begin_resize_drag(Gdk.WindowEdge.SOUTH, button, x_root, y_root, event.get_time());
        }

        draw(_widget, cr) {
            if (!this.app_paintable)
                return false;

            if (!Gtk.cairo_should_draw_window(cr, this.window))
                return false;

            const context = this.get_style_context();
            const allocation = this.get_child().get_allocation();
            Gtk.render_background(context, cr, allocation.x, allocation.y, allocation.width, allocation.height);
            Gtk.render_frame(context, cr, allocation.x, allocation.y, allocation.width, allocation.height);

            return false;
        }

        tab_switcher_add(_notebook, child, page_num) {
            child.switcher_item.action_target = GLib.Variant.new_int32(page_num);
            this.tab_switch_menu_box.add(child.switcher_item);
            this.tab_switch_menu_box.reorder_child(child.switcher_item, page_num);
            this.tab_switcher_update_actions(page_num + 1);
        }

        tab_switcher_remove(_notebook, child, page_num) {
            this.tab_switch_menu_box.remove(child.switcher_item);
            this.tab_switcher_update_actions(page_num);
        }

        tab_switcher_reorder(_notebook, child, page_num) {
            this.tab_switch_menu_box.reorder_child(child.switcher_item, page_num);
            this.tab_switcher_update_actions(page_num);
        }

        tab_switcher_update_actions(start_page_num) {
            const items = this.tab_switch_menu_box.get_children();
            for (let i = start_page_num; i < items.length; i++)
                items[i].action_target = GLib.Variant.new_int32(i);
        }

        update_type_hint() {
            this.type_hint = this.settings.get_enum('window-type-hint');
            // skip_taskbar_hint should always be set after type_hint
            this.skip_taskbar_hint = this.settings.get_boolean('window-skip-taskbar');
        }
    }
);

Object.assign(AppWindow.prototype, util.UtilMixin);
