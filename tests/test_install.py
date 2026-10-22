"""Installer configuration preservation and Docker command boundaries; no Docker/network."""
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy import install


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.template = (install.ROOT / '.env.example').read_text()
        (self.root / '.env.example').write_text(self.template)

    def configure(self, origin='', key='', network=None):
        output = io.StringIO()
        with patch('builtins.input', return_value=origin), \
                patch.object(install.getpass, 'getpass', return_value=key), \
                redirect_stdout(output), redirect_stderr(output):
            install.configure(self.root, network)
        return output.getvalue()

    def test_first_install_requires_own_key_generates_private_config_and_no_secret_output(self):
        with patch.object(install.getpass, 'getpass', side_effect=['', 'invalid&key', 'synthetic-map-key']), \
                patch('builtins.input', return_value='https://checkin.example.com:60000'), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(output):
            install.configure(self.root)
        path = self.root / '.env'
        text = path.read_text()
        secret = install.env_value(text, 'GATEWAY_SECRET')
        self.assertRegex(secret, r'^[a-f0-9]{64}$')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(install.env_value(text, 'TIANDITU_KEY'), 'synthetic-map-key')
        self.assertEqual(install.env_value(text, 'MAP_PROVIDER'), 'tianditu')
        self.assertEqual(install.env_value(text, 'PUBLIC_ORIGIN'), 'https://checkin.example.com:60000')
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn('synthetic-map-key', output.getvalue())

    def test_repeat_install_preserves_existing_secrets_project_and_custom_settings(self):
        text = self.template.replace('https://checkin.example.com', 'https://old.example.com:60000')
        text = text.replace('GATEWAY_SECRET=', "GATEWAY_SECRET='existing-gateway-secret-" + 'x'*32 + "'")
        text = text.replace('TIANDITU_KEY=', 'TIANDITU_KEY=existing-map-key')
        text = text.replace('SESSION_IDLE_SECONDS=1800', 'SESSION_IDLE_SECONDS=900')
        text += '\n# Keep existing volume/project identity\nCOMPOSE_PROJECT_NAME=existing-deployment\nCUSTOM_VALUE=${EXTERNAL_VALUE}\n'
        (self.root / '.env').write_text(text)
        output = self.configure()
        self.assertEqual((self.root / '.env').read_text(), text)
        self.assertNotIn('existing-map-key', output)
        self.assertNotIn('existing-gateway-secret', output)

    def test_cancelled_input_leaves_original_configuration_intact(self):
        (self.root / '.env').write_text(self.template)
        with patch('builtins.input', return_value='https://checkin.example.com'), \
                patch.object(install.getpass, 'getpass', side_effect=KeyboardInterrupt), \
                redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            install.configure(self.root)
        self.assertEqual((self.root / '.env').read_text(), self.template)
        self.assertEqual(list(self.root.glob('.env.install-*')), [])

    def test_switch_to_tianditu_replaces_duplicate_map_assignments_only(self):
        text = self.template + '\nMAP_PROVIDER=osm\nTIANDITU_KEY=old-key\n# custom setting\nMAX_SESSIONS=12\n'
        (self.root / '.env').write_text(text)
        self.configure('https://checkin.example.com', 'new-map-key', 'existing-proxy')
        updated = (self.root / '.env').read_text()
        self.assertEqual(updated.count('\nTIANDITU_KEY='), 1)
        self.assertEqual(updated.count('\nMAP_PROVIDER='), 1)
        self.assertEqual(install.env_value(updated, 'TIANDITU_KEY'), 'new-map-key')
        self.assertEqual(install.env_value(updated, 'PROXY_NETWORK'), 'existing-proxy')
        self.assertIn('# custom setting\nMAX_SESSIONS=12\n', updated)

    def test_environment_is_never_executed_and_symlinks_are_not_replaced(self):
        text = self.template.replace('GATEWAY_SECRET=', 'GATEWAY_SECRET=$(touch should-not-exist)')
        (self.root / '.env').write_text(text)
        with self.assertRaises(ValueError):
            self.configure('https://checkin.example.com', 'synthetic-key')
        self.assertEqual((self.root / '.env').read_text(), text)
        (self.root / '.env').unlink()
        (self.root / '.env').symlink_to(self.root / '.env.example')
        with self.assertRaises(ValueError):
            install.configure(self.root)
        self.assertEqual((self.root / '.env.example').read_text(), self.template)

    def test_application_only_commands_use_saved_settings_and_existing_proxy_network(self):
        with patch.object(install.sys.stdin, 'isatty', return_value=True), \
                patch.object(install, 'check_compose'), \
                patch.object(install, 'configure') as configure, \
                patch.dict('os.environ', {'TIANDITU_KEY': 'stale-shell-key', 'HTTPS_PROXY': 'http://build-proxy'}), \
                patch.object(install.subprocess, 'run') as run, redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(['--proxy-network', 'existing-proxy'], root=self.root), 0)
        configure.assert_called_once_with(self.root, 'existing-proxy')
        calls = run.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].args[0][-2:], ['config', '--quiet'])
        self.assertEqual(calls[1].args[0][-5:], ['up', '-d', '--build', '--wait', 'backend'])
        self.assertEqual(calls[2].args[0][-2:], ['ps', 'backend'])
        for call in calls:
            self.assertIn(str(self.root / 'deploy/compose.proxy-network.yaml'), call.args[0])
            self.assertNotIn('TIANDITU_KEY', call.kwargs['env'])
            self.assertEqual(call.kwargs['env']['HTTPS_PROXY'], 'http://build-proxy')
            self.assertNotIn('stale-shell-key', ' '.join(call.args[0]))

    def test_compose_prerequisites_and_configure_only_do_not_start_containers(self):
        for network, version, valid in ((None, '2.20.0', True), (None, '2.19.0', False),
                                         ('proxy', 'v2.24.3', False), ('proxy', 'v2.24.4', True)):
            with self.subTest(network=network, version=version), \
                    patch.object(install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, version)):
                if valid:
                    install.check_compose(network)
                else:
                    with self.assertRaises(ValueError):
                        install.check_compose(network)
        with patch.object(install.sys.stdin, 'isatty', return_value=True), \
                patch.object(install, 'configure') as configure, \
                patch.object(install.subprocess, 'run') as run:
            self.assertEqual(install.main(['--configure-only'], root=self.root), 0)
        configure.assert_called_once_with(self.root, None)
        run.assert_not_called()
        with patch.object(install.sys.stdin, 'isatty', return_value=False), \
                redirect_stderr(io.StringIO()), patch.object(install, 'configure') as configure:
            self.assertEqual(install.main([], root=self.root), 1)
        configure.assert_not_called()


if __name__ == '__main__':
    unittest.main()
