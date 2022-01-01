import pathlib

from gi.repository import GLib

from . import dbus_proxy, glib_util


def test_proxy(extension_dbus_interface):
    assert extension_dbus_interface.props.WindowVisible == False

    assert isinstance(extension_dbus_interface.props.TargetRect, GLib.Variant)
    assert len(extension_dbus_interface.props.TargetRect) == 4

    method_ret = extension_dbus_interface.GetTargetRect()
    assert isinstance(method_ret, tuple)
    assert len(method_ret) == 4

    extension_dbus_interface.Toggle()
    glib_util.wait_property_value(extension_dbus_interface, 'WindowVisible', True, 5000)
