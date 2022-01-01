import logging

import pytest


LOGGER = logging.getLogger(__name__)


pytestmark = [
    pytest.mark.usefixtures('journal', 'close_welcome_dialog', 'screenshot'),
]


def test_show(extension_dbus_interface):
    assert not extension_dbus_interface.get_cached_property('WindowVisible').unpack()
