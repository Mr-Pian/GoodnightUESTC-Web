import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from Crypto.Cipher import AES

from hosted import HostedSettings
from session_packages import PackageError, SessionPackages
from web_support import SNAPSHOT


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = HostedSettings('https://team.example.com', 'a'*64, max_packages=2)
        self.packages = SessionPackages(self.temp.name, self.settings)

    def tearDown(self):
        self.temp.cleanup()

    def test_absolute_deadline_expiry_and_origin_binding(self):
        token, grant = self.packages.export(SNAPSHOT, 'device1')
        with patch('session_packages.time.time', return_value=grant['expires'] + 1):
            with self.assertRaises(PackageError) as caught:
                self.packages.restore(token, 'device1')
            self.assertEqual(caught.exception.reason, 'saved_session_expired')
        other = SessionPackages(self.temp.name, HostedSettings('https://other.example.com', 'a'*64))
        with self.assertRaises(PackageError):
            other.restore(token, 'device1')

    def test_reissuing_after_real_login_replaces_old_grant_and_metadata_is_bounded(self):
        old, _ = self.packages.export(SNAPSHOT, 'device1')
        current, _ = self.packages.export(SNAPSHOT, 'device1')
        with self.assertRaises(PackageError):
            self.packages.restore(old, 'device1')
        self.assertEqual(self.packages.restore(current, 'device1')[0], SNAPSHOT)
        self.packages.export(SNAPSHOT, 'device2')
        with self.assertRaises(PackageError):
            self.packages.export(SNAPSHOT, 'device3')
        # Replacing the same device's grant is allowed even at global capacity.
        self.packages.export(SNAPSHOT, 'device1')

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

    def test_upgrade_keeps_key_and_legacy_metadata_but_rejects_site_account_packages(self):
        device = 'device1'
        grant = {'id': 'legacy-grant', 'issued': time.time(), 'expires': time.time() + 3600}
        old_identity = (self.packages.tag('owner', 'alice'), self.packages.tag('device', device))
        with self.packages.db() as db:
            db.execute('INSERT INTO packages VALUES (?,?,?,?,?)',
                       (*old_identity, grant['id'], grant['issued'], grant['expires']))
        # Reproduce a genuine old-format package, including its authenticated account binding.
        cipher = AES.new(self.packages.key, AES.MODE_GCM, nonce=b'legacy-nonce')
        cipher.update(json.dumps(['school-session-v1', self.settings.public_origin, 'alice', device],
                                 separators=(',', ':')).encode())
        ciphertext, tag = cipher.encrypt_and_digest(json.dumps({'v': 1, 'grant': grant, 'snapshot': SNAPSHOT}).encode())
        old = base64.urlsafe_b64encode(b'GS1' + cipher.nonce + tag + ciphertext).decode()
        restarted = SessionPackages(self.temp.name, self.settings)
        self.assertEqual(restarted.key, self.packages.key)
        with self.assertRaises(PackageError):
            restarted.restore(old, device)
        token, _ = restarted.export(SNAPSHOT, device)
        self.assertEqual(restarted.restore(token, device)[0], SNAPSHOT)
        with restarted.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM packages WHERE owner=? AND device=?', old_identity).fetchone()[0], 1)

    def test_only_valid_cookie_snapshot_is_packaged_and_password_is_excluded(self):
        token, _ = self.packages.export({**SNAPSHOT, 'password': 'must-not-be-packaged'}, 'device1')
        snapshot, _ = self.packages.restore(token, 'device1')
        self.assertNotIn('password', snapshot)
        for cookies in ([], [{**SNAPSHOT['cookies'][0], 'domain': 'evil.example'}]):
            with self.assertRaises(PackageError):
                self.packages.export({**SNAPSHOT, 'cookies': cookies}, 'device1')


if __name__ == '__main__':
    unittest.main()
