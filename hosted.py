"""Bounded, isolated server-side workspaces; no school credentials on disk."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
import os
import secrets
import threading
import time
from urllib.parse import urlsplit

from browser_login import BrowserLogin
from core import normalize_location
from progress import Progress


@dataclass(frozen=True)
class HostedSettings:
    public_origin: str
    gateway_secret: str
    idle_seconds: int = 1800
    lifetime_seconds: int = 28800
    max_sessions: int = 64
    max_sessions_per_browser: int = 8
    max_logins: int = 2
    package_seconds: int = 604800
    max_packages: int = 1024
    source_rate_multiplier: int = 4

    def __post_init__(self):
        u = urlsplit(self.public_origin)
        u.port  # Validate malformed or out-of-range ports at startup.
        if (u.scheme != 'https' or not u.hostname or u.username or u.password
                or u.path or u.query or u.fragment or u.netloc != u.netloc.lower()):
            raise ValueError('PUBLIC_ORIGIN must be an HTTPS origin without a trailing slash.')
        if len(self.gateway_secret) < 32 or not self.gateway_secret.isascii():
            raise ValueError('GATEWAY_SECRET must contain at least 32 ASCII characters.')
        for key in ('idle_seconds', 'lifetime_seconds', 'max_sessions', 'max_sessions_per_browser', 'max_logins',
                    'package_seconds', 'max_packages', 'source_rate_multiplier'):
            if getattr(self, key) <= 0:
                raise ValueError(key + ' must be positive.')

    @classmethod
    def from_env(cls):
        values = {key: int(os.environ[env]) for key, env in {
            'idle_seconds': 'SESSION_IDLE_SECONDS', 'lifetime_seconds': 'SESSION_LIFETIME_SECONDS',
            'max_sessions': 'MAX_SESSIONS', 'max_sessions_per_browser': 'MAX_SESSIONS_PER_BROWSER',
            'max_logins': 'MAX_CONCURRENT_LOGINS',
            'package_seconds': 'SESSION_PACKAGE_TTL_SECONDS', 'max_packages': 'MAX_SAVED_DEVICES',
            'source_rate_multiplier': 'SOURCE_RATE_MULTIPLIER',
        }.items() if env in os.environ}
        return cls(os.environ['PUBLIC_ORIGIN'], os.environ['GATEWAY_SECRET'], **values)


class MemoryStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.saved_session = None
        self.saved_location = None
        self.closed = False
        self.revision = secrets.token_urlsafe(12)
        self.package = None

    def save_session(self, value):
        with self.lock:
            if not self.closed:
                self.saved_session = deepcopy(value)
                self.revision = secrets.token_urlsafe(12)
                self.package = None

    def update_session(self, value):
        with self.lock:
            current = self.saved_session
            if (not self.closed and current and all(current[k] == value[k] for k in
                    ('student_id', 'saved_at', 'user_agent')) and current['cookies'] != value['cookies']):
                self.saved_session = deepcopy(value)
                self.revision = secrets.token_urlsafe(12)

    def session(self):
        with self.lock:
            return deepcopy(self.saved_session)

    def forget(self):
        with self.lock:
            self.saved_session = None
            self.package = None
            self.revision = secrets.token_urlsafe(12)

    def save_location(self, value):
        result = normalize_location(value)
        with self.lock:
            self.saved_location = result
        return deepcopy(result)

    def location(self):
        with self.lock:
            return deepcopy(self.saved_location)

    def close(self):
        with self.lock:
            self.closed = True
            self.saved_session = self.saved_location = None
            self.package = None


class Workspace:
    def __init__(self, store, login):
        self.store, self.login = store, login
        self.test_lock = threading.Lock()
        self.operation = Progress()
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.csrf = secrets.token_urlsafe(24)
        self.created = self.touched = time.monotonic()
        self.references = 0

    def close(self):
        self.login.cancel()
        self.store.close()
        with self.pending_lock:
            self.pending.clear()


class WorkspaceRegistry:
    def __init__(self, settings, *, reap=True):
        self.settings = settings
        self.lock = threading.RLock()
        self.entries = {}
        self.logins = threading.BoundedSemaphore(settings.max_logins)
        self.stopped = threading.Event()
        if reap:
            threading.Thread(target=self._reaper, daemon=True, name='session-expiry').start()

    def expired(self, entry, instant):
        return (instant - entry.created >= self.settings.lifetime_seconds
                or instant - entry.touched >= self.settings.idle_seconds)

    def prune(self):
        with self.lock:
            instant = time.monotonic()
            for sid, (_, entry) in list(self.entries.items()):
                if not entry.references and self.expired(entry, instant):
                    self.entries.pop(sid)
                    entry.close()

    def acquire(self, sid, device):
        with self.lock:
            self.prune()
            saved = self.entries.get(sid)
            # A workspace cookie alone cannot carry state to another browser device.
            if not saved or saved[0] != device:
                if (len(self.entries) >= self.settings.max_sessions or
                        sum(d == device for d, _ in self.entries.values()) >= self.settings.max_sessions_per_browser):
                    return None, None
                sid = secrets.token_urlsafe(32)
                store = MemoryStore()
                entry = Workspace(store, BrowserLogin(store, slots=self.logins))
                self.entries[sid] = device, entry
            else:
                entry = saved[1]
            entry.references += 1
            entry.touched = time.monotonic()
            return sid, entry

    def release(self, entry):
        with self.lock:
            entry.references -= 1

    def _reaper(self):
        while not self.stopped.wait(10):
            self.prune()

    def close(self):
        self.stopped.set()
        with self.lock:
            for _, entry in self.entries.values():
                entry.close()
            self.entries.clear()


class RateLimits:
    """Bounded time windows for browser devices and trusted proxy source IPs."""
    def __init__(self):
        self.lock = threading.Lock()
        self.events = {}

    def allow(self, identity, category, count, seconds):
        instant = time.monotonic()
        with self.lock:
            for key, events in list(self.events.items()):
                if not events or instant - events[-1] >= 300:
                    self.events.pop(key)
            events = self.events.setdefault((identity, category), deque())
            while events and events[0] <= instant - seconds:
                events.popleft()
            if len(events) >= count:
                return False
            events.append(instant)
            return True
