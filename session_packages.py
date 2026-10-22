"""Browser-held AES-GCM sessions; disk contains a key and revocation metadata only."""
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

from Crypto.Cipher import AES

MAX_PACKAGE_BYTES = 65536
DEVICE_COOKIE = '__Host-uestc-device'


class PackageError(ValueError):
    def __init__(self, reason='saved_session_invalid'):
        self.reason = reason
        super().__init__(reason)


def checked_snapshot(value):
    """Keep only the login snapshot schema, never passwords or arbitrary browser data."""
    try:
        if not isinstance(value, dict) or not re.fullmatch(r'[0-9]{8,20}', value['student_id']):
            raise ValueError()
        agent, saved = value['user_agent'], value['saved_at']
        if not isinstance(agent, str) or not 1 <= len(agent) <= 2048 or '\r' in agent or '\n' in agent:
            raise ValueError()
        if type(saved) not in (int, float) or not math.isfinite(saved):
            raise ValueError()
        if not isinstance(value['cookies'], list) or not 1 <= len(value['cookies']) <= 128:
            raise ValueError()
        cookies = []
        for item in value['cookies']:
            cookie = {key: item[key] for key in ('name', 'value', 'domain', 'path', 'expires', 'secure', 'httpOnly')}
            if (not all(isinstance(cookie[k], str) for k in ('name', 'value', 'domain', 'path'))
                    or cookie['domain'].lstrip('.') not in ('idas.uestc.edu.cn', 'jzsz.uestc.edu.cn', 'uestc.edu.cn')
                    or not cookie['name'] or len(cookie['name']) > 256 or len(cookie['value']) > 16384
                    or not cookie['path'].startswith('/') or len(cookie['path']) > 2048
                    or any(c in cookie[k] for k in ('name', 'value', 'domain', 'path') for c in '\r\n\0')
                    or type(cookie['expires']) not in (int, float) or not math.isfinite(cookie['expires'])
                    or type(cookie['secure']) is not bool or type(cookie['httpOnly']) is not bool):
                raise ValueError()
            if item.get('sameSite') in ('Strict', 'Lax', 'None'):
                cookie['sameSite'] = item['sameSite']
            cookies.append(cookie)
        if not any(c['name'] == 'SESSION' and c['domain'].lstrip('.') == 'jzsz.uestc.edu.cn' for c in cookies):
            raise ValueError()
        return {'student_id': value['student_id'], 'user_agent': agent, 'saved_at': saved, 'cookies': cookies}
    except (KeyError, TypeError, ValueError):
        raise PackageError() from None


class SessionPackages:
    def __init__(self, directory, settings):
        self.directory = Path(directory)
        self.path = self.directory / 'session-packages.sqlite3'
        self.settings = settings
        key_path = self.directory / 'session-package.key'
        if not key_path.exists():
            if self.path.exists():
                raise ValueError('Session package key is missing; restore the key before starting.')
            fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(secrets.token_bytes(32))
                stream.flush()
                os.fsync(stream.fileno())
        self.key = key_path.read_bytes()
        if len(self.key) != 32:
            raise ValueError('Invalid session package key.')
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS packages ('
                       'owner TEXT, device TEXT, grant_id TEXT NOT NULL, issued REAL NOT NULL, '
                       'expires REAL NOT NULL, PRIMARY KEY(owner, device))')
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def tag(self, purpose, value):
        return hmac.new(self.key, (purpose + '\0' + value).encode(), hashlib.sha256).hexdigest()

    def identity(self, device):
        # Retain the SQL schema so upgrades preserve the key, grants and attempt log.
        # Old site-account grants expire normally; only GS2 browser grants are usable here.
        return 'browser-v2', self.tag('device', device)

    def aad(self, device):
        return json.dumps(['school-session-v2', self.settings.public_origin, device],
                          ensure_ascii=True, separators=(',', ':')).encode()

    def current(self, grant, device):
        if not grant:
            return False
        with self.db() as db:
            row = db.execute('SELECT grant_id, issued, expires FROM packages WHERE owner=? AND device=?',
                             self.identity(device)).fetchone()
        return row == (grant['id'], grant['issued'], grant['expires']) and row[2] > time.time()

    def revoke(self, device):
        with self.db() as db:
            db.execute('DELETE FROM packages WHERE owner=? AND device=?', self.identity(device))

    def export(self, snapshot, device, grant=None):
        snapshot = checked_snapshot(snapshot)
        instant = time.time()
        fresh = grant is None
        if grant is not None:
            if not self.current(grant, device):
                raise PackageError('saved_session_unavailable')
        else:
            grant = {'id': secrets.token_urlsafe(24), 'issued': instant,
                     'expires': instant + self.settings.package_seconds}
        payload = json.dumps({'v': 2, 'grant': grant, 'snapshot': snapshot},
                             separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
        # Bound allocations before encryption and the JSON request on restoration.
        if len(payload) > 48000:
            raise PackageError('saved_session_too_large')
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=secrets.token_bytes(12))
        cipher.update(self.aad(device))
        encrypted, tag = cipher.encrypt_and_digest(payload)
        token = base64.urlsafe_b64encode(b'GS2' + cipher.nonce + tag + encrypted).decode()
        if len(token) > MAX_PACKAGE_BYTES:
            raise PackageError('saved_session_too_large')
        if fresh:
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM packages WHERE expires<=?', (instant,))
                identity = self.identity(device)
                present = db.execute('SELECT 1 FROM packages WHERE owner=? AND device=?', identity).fetchone()
                if not present and db.execute('SELECT COUNT(*) FROM packages').fetchone()[0] >= self.settings.max_packages:
                    raise PackageError('saved_session_capacity')
                db.execute('INSERT OR REPLACE INTO packages VALUES (?,?,?,?,?)',
                           (*identity, grant['id'], grant['issued'], grant['expires']))
        return token, grant

    def restore(self, token, device):
        if not isinstance(token, str) or not 40 <= len(token) <= MAX_PACKAGE_BYTES:
            raise PackageError()
        try:
            raw = base64.b64decode(token, altchars=b'-_', validate=True)
            if raw[:3] != b'GS2' or len(raw) < 32:
                raise ValueError()
            cipher = AES.new(self.key, AES.MODE_GCM, nonce=raw[3:15])
            cipher.update(self.aad(device))
            payload = json.loads(cipher.decrypt_and_verify(raw[31:], raw[15:31]))
            if payload['v'] != 2 or not isinstance(payload['grant'], dict):
                raise ValueError()
            grant = payload['grant']
            if (set(grant) != {'id', 'issued', 'expires'} or not isinstance(grant['id'], str)
                    or any(type(grant[k]) not in (int, float) or not math.isfinite(grant[k]) for k in ('issued', 'expires'))):
                raise ValueError()
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise PackageError() from None
        if grant['expires'] <= time.time():
            raise PackageError('saved_session_expired')
        if not self.current(grant, device):
            raise PackageError('saved_session_unavailable')
        return checked_snapshot(payload['snapshot']), grant
