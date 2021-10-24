import io
import logging
import subprocess
import tarfile

import pytest
import wand.image


LOGGER = logging.getLogger(__name__)


pytestmark = [
    pytest.mark.usefixtures('journal'),
]


@pytest.fixture(scope='session', autouse=True)
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


@pytest.fixture(scope='session', autouse=True)
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


def test_show(extension_dbus_interface):
    pass
