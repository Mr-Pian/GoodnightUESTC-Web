"""Hosted isolation and resource limits. All school traffic is synthetic."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from app import create_app
from browser_login import BrowserLogin
from core import ReadOnlyTransport, ToolError
from hosted import HostedSettings, MemoryStore, WorkspaceRegistry
from session_packages import DEVICE_COOKIE
from web_support import SNAPSHOT, POINT, INSTANT, DAY, BASE, GATEWAY


class HostedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = HostedSettings(BASE, GATEWAY['X-Team-Gateway'])
        self.app = create_app(self.temp.name, hosted=self.settings)
        self.registry = self.app.extensions['workspaces']
        self.a, self.b = self.app.test_client(), self.app.test_client()
        self.ca, self.cb = self.state(self.a)['csrf'], self.state(self.b)['csrf']

    def tearDown(self):
        self.registry.close()
        self.temp.cleanup()

    def state(self, client, **headers):
        return client.get('/api/status', base_url=BASE, headers={**GATEWAY, **headers}).json

    def post(self, client, csrf, path, data, **headers):
        return client.post(path, base_url=BASE, json=data,
                           headers={**GATEWAY, 'Origin': BASE, 'X-CSRF-Token': csrf, **headers})

    def workspace(self, client):
        with client.session_transaction(base_url=BASE) as cookie:
            return self.registry.entries[cookie['workspace']][1]

    def seed(self, client, student=None):
        entry = self.workspace(client)
        entry.store.save_session({**SNAPSHOT, 'student_id': student or SNAPSHOT['student_id']})
        entry.store.save_location(POINT)
        return entry

    def test_gateway_host_origin_and_csrf_all_enforced(self):
        for headers in ({}, {'X-Team-User': 'alice'}, {**GATEWAY, 'X-Team-Gateway': 'bad'}):
            self.assertEqual(self.a.get('/api/status', base_url=BASE, headers=headers).status_code, 403)
        self.assertEqual(self.a.get('/api/status', base_url='https://wrong.example', headers=GATEWAY).status_code, 403)
        for headers in ({'Origin': 'https://wrong.example'}, {'Origin': 'http://team.example.com'},
                        {'X-CSRF-Token': ''}, {'X-CSRF-Token': self.cb}):
            self.assertEqual(self.post(self.a, self.ca, '/api/location', POINT, **headers).status_code, 403)
        self.assertEqual(self.post(self.a, self.ca, '/api/location', POINT).status_code, 200)

    def test_cookie_is_secure_and_contains_no_school_credentials(self):
        response = self.app.test_client().get('/api/status', base_url=BASE, headers=GATEWAY)
        cookies = response.headers.getlist('Set-Cookie')
        cookie = next(c for c in cookies if c.startswith('__Host-uestc='))
        for value in ('__Host-uestc=', 'Secure', 'HttpOnly', 'SameSite=Strict', 'Path=/'):
            self.assertIn(value, cookie)
        self.assertNotIn('Domain=', cookie)
        device = next(c for c in cookies if c.startswith(DEVICE_COOKIE + '='))
        self.assertIn('Max-Age=604800', device)
        self.assertIn('HttpOnly', device)
        self.seed(self.a)
        public = json.dumps(self.state(self.a)) + cookie
        self.assertNotIn('synthetic-cookie-secret', public)
        self.assertNotIn('cookies', public)

    def test_store_login_progress_confirmation_and_transport_are_isolated(self):
        wa = self.seed(self.a)
        wb = self.workspace(self.b)
        self.assertIsNot(wa.login, wb.login)
        self.assertIsNot(wa.pending, wb.pending)
        self.assertIsNot(wa.test_lock, wb.test_lock)
        self.assertIsNot(wa.store, wb.store)
        wa.login.update('waiting', 'synthetic alice status')
        wa.operation.step('synthetic alice operation')
        self.assertIsNone(self.state(self.b)['account'])
        self.assertIsNone(self.state(self.b)['location'])
        self.assertEqual(self.state(self.b)['login']['state'], 'idle')
        with patch('app.online_test', return_value={'schoolWrites': 0}) as school:
            self.assertEqual(self.post(self.a, self.ca, '/api/test/online', {}).status_code, 200)
            self.assertEqual(self.post(self.b, self.cb, '/api/test/online', {}).status_code, 400)
            school.assert_called_once()
            self.assertEqual(school.call_args.args[0], SNAPSHOT)
        ta, tb = ReadOnlyTransport(SNAPSHOT), ReadOnlyTransport({**SNAPSHOT, 'cookies': []})
        self.assertIsNot(ta.jar, tb.jar)
        self.assertEqual(len(tb.jar), 0)

    def test_same_gateway_user_has_independent_browsers_and_new_identity_cannot_reuse_cookie(self):
        self.seed(self.a)
        self.assertIsNone(self.state(self.b)['account'])
        changed = self.state(self.a, **{'X-Team-User': 'bob'})
        self.assertIsNone(changed['account'])
        self.assertNotEqual(changed['csrf'], self.ca)

    def test_confirmation_cannot_cross_workspaces_and_is_invalidated_by_relogin(self):
        wa = self.seed(self.a)
        self.seed(self.b, '202300000002')
        with patch('app.now', return_value=INSTANT), patch('app.prepare_checkin', return_value={'businessDate': DAY}):
            token = self.post(self.a, self.ca, '/api/checkin/prepare', {}).json['confirmation']
            data = {'confirmation': token, 'confirm': True, 'actual_location_confirmed': True}
            with patch('app.submit_checkin', return_value={}) as submit:
                self.assertEqual(self.post(self.b, self.cb, '/api/checkin', data).status_code, 400)
                submit.assert_not_called()
                self.assertEqual(self.post(self.a, self.ca, '/api/checkin', data).status_code, 200)
                submit.assert_called_once()
            token = self.post(self.a, self.ca, '/api/checkin/prepare', {}).json['confirmation']
            wa.store.save_session({**SNAPSHOT, 'cookies': [{**SNAPSHOT['cookies'][0], 'value': 'new-session'}]})
            with patch('app.submit_checkin') as submit:
                self.assertEqual(self.post(self.a, self.ca, '/api/checkin', {**data, 'confirmation': token}).status_code, 400)
                submit.assert_not_called()

    def test_busy_user_does_not_block_other_user_or_allow_credential_changes(self):
        wa = self.seed(self.a)
        self.seed(self.b)
        with wa.test_lock, patch('app.online_test', return_value={}), patch('browser_login.BrowserLogin.start') as login:
            self.assertEqual(self.post(self.a, self.ca, '/api/location', POINT).status_code, 409)
            self.assertEqual(self.post(self.a, self.ca, '/api/session/forget', {}).status_code, 409)
            self.assertEqual(self.post(self.a, self.ca, '/api/login', {'student_id': SNAPSHOT['student_id'], 'password': 'synthetic'}).status_code, 409)
            self.assertEqual(self.post(self.b, self.cb, '/api/test/online', {}).status_code, 200)
            login.assert_not_called()

    def test_frames_and_input_are_isolated_and_never_cached(self):
        wa = self.workspace(self.a)
        wa.login.frame = b'synthetic-jpeg'
        response = self.a.get('/api/login/frame', base_url=BASE, headers=GATEWAY)
        self.assertTrue(response.json['image'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIsNone(self.b.get('/api/login/frame', base_url=BASE, headers=GATEWAY).json['image'])
        with patch.object(wa.login, 'command') as command:
            self.assertEqual(self.post(self.a, self.ca, '/api/login/input', {'kind': 'click', 'x': 1, 'y': 2}).status_code, 202)
            command.assert_called_once()
        self.assertEqual(self.post(self.b, self.cb, '/api/login/input', {'kind': 'text', 'text': 'synthetic'}).status_code, 400)

    def test_no_credentials_or_locations_are_written_to_data_directory(self):
        self.seed(self.a)
        self.post(self.a, self.ca, '/api/location', POINT)
        self.assertEqual({p.name for p in Path(self.temp.name).iterdir()},
                         {'attempts.sqlite3', 'session-package.key', 'session-packages.sqlite3'})
        for path in Path(self.temp.name).iterdir():
            self.assertNotIn(b'synthetic-cookie-secret', path.read_bytes())
            self.assertNotIn(SNAPSHOT['student_id'].encode(), path.read_bytes())
        self.assertEqual(self.post(self.a, self.ca, '/api/session/forget', {}).status_code, 200)
        self.assertIsNone(self.state(self.a)['account'])

    def test_expiry_cancels_login_discards_state_and_rejects_stale_csrf(self):
        entry = self.seed(self.a)
        entry.created -= self.settings.lifetime_seconds + 1
        with patch.object(entry.login, 'cancel') as cancel:
            response = self.post(self.a, self.ca, '/api/location', POINT)
            self.assertEqual(response.status_code, 403)
            cancel.assert_called_once()
        self.assertIsNone(entry.store.session())
        entry.store.save_session(SNAPSHOT)  # A late login thread cannot resurrect it.
        self.assertIsNone(entry.store.session())
        self.assertIsNone(self.state(self.a)['account'])

    def test_login_rate_limit_survives_new_browser_sessions(self):
        with patch('browser_login.BrowserLogin.start'):
            for _ in range(3):
                self.assertEqual(self.post(self.a, self.ca, '/api/login', {'student_id': SNAPSHOT['student_id'], 'password': 'synthetic'}).status_code, 202)
            response = self.post(self.b, self.cb, '/api/login', {'student_id': SNAPSHOT['student_id'], 'password': 'synthetic'})
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response.headers)

    def test_unexpected_exceptions_do_not_expose_secrets(self):
        self.seed(self.a)
        with patch('app.online_test', side_effect=RuntimeError('synthetic-cookie-secret')):
            response = self.post(self.a, self.ca, '/api/test/online', {})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('synthetic-cookie-secret', response.get_data(as_text=True))

    def exported(self):
        self.seed(self.a)
        response = self.post(self.a, self.ca, '/api/session/package', {})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('synthetic-cookie-secret', response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.json

    def expire_workspace(self):
        self.workspace(self.a).created -= self.settings.lifetime_seconds + 1
        self.ca = self.state(self.a)['csrf']
        self.assertIsNone(self.state(self.a)['account'])

    def test_restore_after_workspace_cleanup_uses_only_readonly_validation(self):
        package = self.exported()
        self.expire_workspace()
        with patch('app.verify_session') as verify, patch.object(BrowserLogin, 'start') as login:
            response = self.post(self.a, self.ca, '/api/session/restore', {'token': package['token']})
        self.assertEqual(response.status_code, 200)
        verify.assert_called_once()
        login.assert_not_called()
        self.assertEqual(self.state(self.a)['account']['student_id'], SNAPSHOT['student_id'])
        self.assertIsNone(self.state(self.a)['location'])
        self.assertEqual(response.json['expires_at'], package['expires_at'])

    def test_package_restores_after_backend_restart_without_old_workspace_cookie(self):
        package = self.exported()
        device = self.a.get_cookie(DEVICE_COOKIE, domain='team.example.com').value
        self.registry.close()
        restarted = create_app(self.temp.name, hosted=self.settings)
        try:
            client = restarted.test_client()
            client.set_cookie(DEVICE_COOKIE, device, domain='team.example.com', secure=True)
            csrf = self.state(client)['csrf']
            with patch('app.verify_session'):
                response = self.post(client, csrf, '/api/session/restore', {'token': package['token']})
            self.assertEqual(response.status_code, 200)
            self.assertIsNotNone(self.state(client)['account'])
        finally:
            restarted.extensions['workspaces'].close()

    def test_wrong_device_identity_tampering_and_missing_csrf_never_reach_school(self):
        package = self.exported()
        self.expire_workspace()
        bad = package['token'][:40] + ('A' if package['token'][40] != 'A' else 'B') + package['token'][41:]
        with patch('app.verify_session') as verify:
            self.assertEqual(self.post(self.b, self.cb, '/api/session/restore', {'token': package['token']}).status_code, 400)
            self.assertEqual(self.post(self.a, self.ca, '/api/session/restore', {'token': bad}).status_code, 400)
            self.assertEqual(self.post(self.a, '', '/api/session/restore', {'token': package['token']}).status_code, 403)
            bob = self.state(self.a, **{'X-Team-User': 'bob'})
            response = self.post(self.a, bob['csrf'], '/api/session/restore', {'token': package['token']}, **{'X-Team-User': 'bob'})
            self.assertEqual(response.status_code, 400)
            verify.assert_not_called()

    def test_disable_remember_revokes_package_but_keeps_current_memory_login(self):
        package = self.exported()
        self.assertEqual(self.post(self.a, self.ca, '/api/session/package/forget', {}).status_code, 200)
        self.assertIsNotNone(self.state(self.a)['account'])
        self.expire_workspace()
        with patch('app.verify_session') as verify:
            response = self.post(self.a, self.ca, '/api/session/restore', {'token': package['token']})
            self.assertEqual(response.json['reason'], 'saved_session_unavailable')
            verify.assert_not_called()

    def test_clear_school_session_revokes_even_a_copied_package(self):
        package = self.exported()
        self.assertEqual(self.post(self.a, self.ca, '/api/session/forget', {}).status_code, 200)
        with patch('app.verify_session') as verify:
            self.assertEqual(self.post(self.a, self.ca, '/api/session/restore', {'token': package['token']}).status_code, 400)
            verify.assert_not_called()

    def test_transient_restore_failure_keeps_package_valid_for_manual_retry(self):
        package = self.exported()
        self.expire_workspace()
        with patch('app.verify_session', side_effect=ToolError('timeout')):
            response = self.post(self.a, self.ca, '/api/session/restore', {'token': package['token']})
        self.assertEqual(response.json['reason'], 'timeout')
        self.assertIsNone(self.state(self.a)['account'])
        with patch('app.verify_session'):
            self.assertEqual(self.post(self.a, self.ca, '/api/session/restore', {'token': package['token']}).status_code, 200)

    def test_school_rejection_revokes_saved_package(self):
        package = self.exported()
        self.expire_workspace()
        with patch('app.verify_session', side_effect=ToolError('session_rejected')):
            self.assertEqual(self.post(self.a, self.ca, '/api/session/restore', {'token': package['token']}).status_code, 400)
        with patch('app.verify_session') as verify:
            response = self.post(self.a, self.ca, '/api/session/restore', {'token': package['token']})
            self.assertEqual(response.json['reason'], 'saved_session_unavailable')
            verify.assert_not_called()

    def test_cookie_updates_are_saved_without_extending_package_deadline(self):
        package = self.exported()
        def rotate(snapshot, *args, **kwargs):
            snapshot['cookies'][0]['value'] = 'synthetic-rotated-cookie'
            return {'schoolWrites': 0}
        with patch('app.online_test', side_effect=rotate):
            self.assertEqual(self.post(self.a, self.ca, '/api/test/online', {}).status_code, 200)
        updated = self.post(self.a, self.ca, '/api/session/package', {}).json
        self.assertEqual(updated['expires_at'], package['expires_at'])
        self.expire_workspace()
        with patch('app.verify_session') as verify:
            self.assertEqual(self.post(self.a, self.ca, '/api/session/restore', {'token': updated['token']}).status_code, 200)
            self.assertEqual(verify.call_args.args[0]['cookies'][0]['value'], 'synthetic-rotated-cookie')

    def test_restore_body_is_bounded_without_changing_other_endpoint_limit(self):
        self.assertEqual(self.post(self.a, self.ca, '/api/location', {'name': 'x'*17000}).status_code, 413)
        self.assertEqual(self.post(self.a, self.ca, '/api/session/restore', {'token': 'x'*66000}).status_code, 400)
        self.assertEqual(self.post(self.a, self.ca, '/api/session/restore', {'token': 'x'*100000}).status_code, 413)


class ResourceTests(unittest.TestCase):
    def test_active_requests_are_leased_and_capacity_is_bounded(self):
        registry = WorkspaceRegistry(HostedSettings(BASE, 'a'*32, max_sessions=1), reap=False)
        try:
            sid, entry = registry.acquire(None, 'alice')
            entry.created -= 100000
            registry.prune()
            self.assertIn(sid, registry.entries)
            self.assertEqual(registry.acquire(None, 'bob'), (None, None))
            registry.release(entry)
            registry.prune()
            self.assertNotIn(sid, registry.entries)
            self.assertIsNotNone(registry.acquire(None, 'bob')[1])
        finally:
            registry.close()

    def test_login_slots_are_bounded_and_released_on_failure(self):
        slots = threading.BoundedSemaphore(1)
        login = BrowserLogin(MemoryStore(), slots=slots)
        with patch('threading.Thread.start'):
            login.start(SNAPSHOT['student_id'], 'synthetic')
            other = BrowserLogin(MemoryStore(), slots=slots)
            with self.assertRaises(RuntimeError):
                other.start(SNAPSHOT['student_id'], 'synthetic')
        with patch('playwright.sync_api.sync_playwright', side_effect=RuntimeError('synthetic')):
            login.run(SNAPSHOT['student_id'], 'synthetic')
        self.assertTrue(slots.acquire(blocking=False))
        slots.release()

    def test_remote_input_rejects_code_urls_and_out_of_bounds_coordinates(self):
        login = BrowserLogin(MemoryStore())
        login.thread = MagicMock()
        for action in ({'kind': 'eval', 'text': 'document.cookie'}, {'kind': 'goto', 'url': BASE},
                       {'kind': 'click', 'x': 1000, 'y': 0}, {'kind': 'click', 'x': float('nan'), 'y': 0},
                       {'kind': 'key', 'key': 'Control+L'}, {'kind': 'text', 'text': 'x'*257}):
            with self.subTest(action=action), self.assertRaises(ValueError):
                login.command(action)
        login.command({'kind': 'click', 'x': 20, 'y': 30})
        login.command({'kind': 'text', 'text': 'synthetic-code'})
        page = MagicMock()
        login.interact(page)
        page.mouse.click.assert_called_once_with(20, 30)
        page.keyboard.insert_text.assert_called_once_with('synthetic-code')
        login.cancel()
        self.assertIsNone(login.screenshot())

    def test_remote_login_uses_new_headless_context_and_saves_only_verified_cookie(self):
        store = MemoryStore()
        login = BrowserLogin(store)
        with patch('playwright.sync_api.sync_playwright') as playwright, \
                patch('browser_login.NETWORK.browser_options', return_value={}), \
                patch('browser_login.verify_session') as verify:
            chromium = playwright.return_value.__enter__.return_value.chromium
            browser = chromium.launch.return_value
            context = browser.new_context.return_value
            context.cookies.return_value = SNAPSHOT['cookies']
            page = context.new_page.return_value
            page.url = 'https://jzsz.uestc.edu.cn/'
            page.is_closed.return_value = False
            page.evaluate.return_value = SNAPSHOT['user_agent']
            login.run(SNAPSHOT['student_id'], 'synthetic-password')
        self.assertTrue(chromium.launch.call_args.kwargs['headless'])
        self.assertNotIn('channel', chromium.launch.call_args.kwargs)
        chromium.launch_persistent_context.assert_not_called()
        browser.new_context.assert_called_once()
        context.route.assert_called_once()
        verify.assert_called_once()
        self.assertEqual(store.session()['cookies'], SNAPSHOT['cookies'])
        self.assertNotIn('password', store.session())
        browser.close.assert_called_once()
        self.assertIsNone(login.screenshot())

    def test_server_settings_reject_http_missing_secrets_and_unbounded_values(self):
        for origin, secret in (('http://team.example.com', 'a'*32), (BASE+'/', 'a'*32), (BASE, 'short')):
            with self.assertRaises(ValueError):
                HostedSettings(origin, secret)
        with self.assertRaises(ValueError):
            HostedSettings(BASE, 'a'*32, max_logins=0)


if __name__ == '__main__':
    unittest.main()
