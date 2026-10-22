"""Synthetic Web fixtures shared by unit tests and browser smoke tests."""
import datetime as dt
import os
import tempfile
import unittest

from app import create_app
from hosted import HostedSettings

BASE = 'https://team.example.com'
GATEWAY = {'X-Team-Gateway': 'a'*64, 'X-Client-IP': '192.0.2.1'}
SNAPSHOT = {'student_id': '202300000001', 'saved_at': 123,
            'user_agent': 'Synthetic Test Browser', 'cookies': [
                {'name': 'SESSION', 'value': 'synthetic-cookie-secret', 'domain': 'jzsz.uestc.edu.cn',
                 'path': '/', 'expires': -1, 'secure': True, 'httpOnly': True}]}
POINT = {'name': '测试位置', 'latitude': 30.7528, 'longitude': 103.9313, 'coordinate_system': 'wgs84'}
INSTANT = dt.datetime(2026, 10, 21, 22, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
DAY = '2026-10-21'


def browser_options():
    """Use bundled Chromium by default; optionally select a local test channel."""
    channel = os.environ.get('TEST_BROWSER_CHANNEL')
    return {'channel': channel} if channel else {}


class WebAppTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name, hosted=HostedSettings(BASE, GATEWAY['X-Team-Gateway']))
        self.registry = self.app.extensions['workspaces']
        self.addCleanup(self.registry.close)
        self.client = self.app.test_client()
        self.status = self.client.get('/api/status', base_url=BASE, headers=GATEWAY).json
        self.headers = {**GATEWAY, 'Origin': BASE, 'X-CSRF-Token': self.status['csrf']}
        with self.client.session_transaction(base_url=BASE) as cookie:
            self.workspace = self.registry.entries[cookie['workspace']][1]
        self.store = self.workspace.store

    def post(self, path, value):
        return self.client.post(path, json=value, base_url=BASE, headers=self.headers)
