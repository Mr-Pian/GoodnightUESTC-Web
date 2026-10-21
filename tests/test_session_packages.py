import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hosted import HostedSettings
from session_packages import PackageError, SessionPackages
from test_readonly import SNAPSHOT


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = HostedSettings('https://team.example.com', 'a'*64, max_packages=2, max_packages_per_user=1)
        self.packages = SessionPackages(self.temp.name, self.settings)

    def tearDown(self):
        self.temp.cleanup()

    def test_absolute_deadline_expiry_and_origin_binding(self):
        token, grant = self.packages.export(SNAPSHOT, 'alice', 'device1')
        with patch('session_packages.time.time', return_value=grant['expires'] + 1):
            with self.assertRaises(PackageError) as caught:
                self.packages.restore(token, 'alice', 'device1')
            self.assertEqual(caught.exception.reason, 'saved_session_expired')
        other = SessionPackages(self.temp.name, HostedSettings('https://other.example.com', 'a'*64))
        with self.assertRaises(PackageError):
            other.restore(token, 'alice', 'device1')

    def test_reissuing_after_real_login_replaces_old_grant_and_metadata_is_bounded(self):
        old, _ = self.packages.export(SNAPSHOT, 'alice', 'device1')
        current, _ = self.packages.export(SNAPSHOT, 'alice', 'device1')
        with self.assertRaises(PackageError):
            self.packages.restore(old, 'alice', 'device1')
        self.assertEqual(self.packages.restore(current, 'alice', 'device1')[0], SNAPSHOT)
        with self.assertRaises(PackageError):
            self.packages.export(SNAPSHOT, 'alice', 'device2')
        self.packages.export(SNAPSHOT, 'bob', 'device3')
        with self.assertRaises(PackageError):
            self.packages.export(SNAPSHOT, 'charlie', 'device4')

    def test_missing_or_invalid_key_fails_without_replacing_it(self):
        key = Path(self.temp.name) / 'session-package.key'
        key.unlink()
        with self.assertRaises(ValueError):
            SessionPackages(self.temp.name, self.settings)
        self.assertFalse(key.exists())
        key.write_bytes(b'bad')
        with self.assertRaises(ValueError):
            SessionPackages(self.temp.name, self.settings)
        self.assertEqual(key.read_bytes(), b'bad')

    def test_only_valid_cookie_snapshot_is_packaged_and_password_is_excluded(self):
        token, _ = self.packages.export({**SNAPSHOT, 'password': 'must-not-be-packaged'}, 'alice', 'device1')
        snapshot, _ = self.packages.restore(token, 'alice', 'device1')
        self.assertNotIn('password', snapshot)
        for cookies in ([], [{**SNAPSHOT['cookies'][0], 'domain': 'evil.example'}]):
            with self.assertRaises(PackageError):
                self.packages.export({**SNAPSHOT, 'cookies': cookies}, 'alice', 'device1')


if __name__ == '__main__':
    unittest.main()
