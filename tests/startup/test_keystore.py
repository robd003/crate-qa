import os
import subprocess
import unittest
from crate.qa.tests import NodeProvider


class SettingsTest(NodeProvider, unittest.TestCase):

    def test_secure_settings(self):
        settings = {
            'path.data': self.mkdtemp(),
            'path.logs': self.mkdtemp(),
            'cluster.name': 'crate',
        }
        # TODO replace this with CRATE_VERSION after the secure settings are released
        node = self._new_node("file:///Users/andrei/dev/crate-3.0.0-SNAPSHOT-250e19b608.tar.gz", settings=settings)

        keystore_script = os.path.join(node.crate_dir, 'bin', 'crate-keystore')

        create_keystore_cmd = 'echo "y" | ' + keystore_script + ' create'
        process = subprocess.Popen(create_keystore_cmd, shell=True, stdout=subprocess.PIPE, close_fds=True)
        process.wait()
        process.terminate()

        add_setting_cmd = 'echo "setting_value" | ' + keystore_script + ' add --stdin --force a.b.c'
        process = subprocess.Popen(add_setting_cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, close_fds=True)
        process.wait()
        process.terminate()

        process = subprocess.Popen(keystore_script + ' list', shell=True, stdout=subprocess.PIPE, close_fds=True)
        keystore_settings = list()
        for stdout_line in iter(process.stdout.readline, ""):
            if stdout_line:
                setting = stdout_line.decode("utf-8").rstrip()
                keystore_settings.append(setting)
            else:
                break
        process.wait()
        process.terminate()

        process = subprocess.Popen(keystore_script + ' remove a.a.a', shell=True, stdout=subprocess.PIPE, close_fds=True)
        process.wait()
        process.terminate()

        self.assertTrue('a.b.c' in keystore_settings)

