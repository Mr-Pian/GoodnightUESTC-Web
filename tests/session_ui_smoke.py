"""Browser UI, synthetic sessions and storage faults; no school network access."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_app
from core import ToolError
from hosted import HostedSettings
from playwright.sync_api import expect, sync_playwright
from web_support import POINT, SNAPSHOT, browser_options
from waitress import create_server

BASE = 'https://team.example.com'
SECRET = 'synthetic-gateway-secret-' * 3
MESSAGE = '#remember-session-message'


class Backend:
    def __init__(self, directory):
        self.directory = directory
        self.start()

    def start(self):
        self.app = create_app(self.directory, hosted=HostedSettings(BASE, SECRET))
        self.server = create_server(self.app, host='127.0.0.1', port=0, threads=8)
        self.worker = threading.Thread(target=self.server.run, daemon=True)
        self.worker.start()

    def close(self):
        self.server.close()
        self.app.extensions['workspaces'].close()
        self.worker.join(timeout=3)

    def workspace(self, owner):
        return next(entry for user, entry in self.app.extensions['workspaces'].entries.values() if user == owner)

    def expire(self, owner):
        self.workspace(owner).created -= 28801


def main():
    errors, requests = [], []
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
        backend = Backend(directory)
        browser = p.chromium.launch(headless=True, **browser_options())
        try:
            def context_for(owner, script='', mobile=False):
                context = browser.new_context(viewport={'width': 390, 'height': 844} if mobile else
                                               {'width': 1440, 'height': 1200})
                if script:
                    context.add_init_script(script)

                def proxy(route):
                    url = route.request.url
                    if not url.startswith(BASE + '/'):
                        route.abort()
                        return
                    requests.append((owner, url.removeprefix(BASE)))
                    response = route.fetch(
                        url=url.replace(BASE, 'http://127.0.0.1:' + str(backend.server.effective_port)),
                        headers={**route.request.headers, 'host': 'team.example.com',
                                 'X-Team-Gateway': SECRET, 'X-Team-User': owner})
                    route.fulfill(response=response)

                context.route('**/*', proxy)
                context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
                return context

            def open_page(context):
                page = context.new_page()
                page.goto(BASE, wait_until='networkidle')
                expect(page.locator('#remember-session-control')).to_be_visible()
                return page

            def seed(page, owner):
                backend.workspace(owner).store.save_session(deepcopy(SNAPSHOT))
                page.evaluate('refresh()')

            def record(page):
                return page.evaluate("async () => SchoolSessionStorage.get((await api('/api/status')).persistence.owner)")['record']

            def stored(page, mode='IndexedDB'):
                expect(page.locator(MESSAGE)).to_contain_text('已加密保存在此浏览器')
                expect(page.locator(MESSAGE)).to_contain_text(mode)
                saved = record(page)
                assert saved['token'] and SNAPSHOT['cookies'][0]['value'] not in saved['token']
                return saved

            with patch('app.verify_session') as verify:
                context = context_for('alice')
                page = open_page(context)
                seed(page, 'alice')
                original = stored(page)
                page.evaluate('(point) => api("/api/location", point)', POINT)
                backend.expire('alice')
                page.evaluate('refresh()')
                expect(page.locator(MESSAGE)).to_contain_text('学校登录已恢复')
                expect(page.locator('#account-label')).to_contain_text(SNAPSHOT['student_id'])
                assert verify.call_count == 1
                state = page.evaluate("api('/api/status')")
                assert state['location'] is None and state['persistence']['expires_at'] == original['expires_at']
                assert not backend.workspace('alice').pending

                # Server restart keeps only the volume; a new page reads browser storage.
                page.close()
                backend.close()
                backend.start()
                page = open_page(context)
                expect(page.locator(MESSAGE)).to_contain_text('学校登录已恢复')
                expect(page.locator('#account-label')).to_contain_text(SNAPSHOT['student_id'])
                assert record(page)['expires_at'] == original['expires_at']
                assert verify.call_count == 2

                # A transient school failure retains the package and offers one manual retry.
                backend.expire('alice')
                verify.side_effect = ToolError('timeout')
                page.evaluate('refresh()')
                expect(page.locator(MESSAGE)).to_contain_text('学校请求超时')
                expect(page.locator('#retry-session-restore')).to_be_visible()
                retained = record(page)
                calls = verify.call_count
                page.evaluate('refresh()')
                assert verify.call_count == calls and retained['token']
                verify.side_effect = None
                page.locator('#retry-session-restore').click()
                expect(page.locator(MESSAGE)).to_contain_text('学校登录已恢复')

                other = open_page(context)
                expect(other.locator(MESSAGE)).to_contain_text('已加密保存在此浏览器')
                page.locator('#remember-session').uncheck()
                expect(page.locator(MESSAGE)).to_contain_text('已撤销')
                expect(other.locator('#remember-session')).not_to_be_checked()
                assert not record(page).get('token')
                assert backend.workspace('alice').store.session()
                page.locator('#forget').click()
                expect(page.locator('#account-label')).to_have_text('等待导入学校账号')
                reason = page.evaluate("token => api('/api/session/restore', {token}).catch(error => error.reason)", retained['token'])
                assert reason == 'saved_session_unavailable'
                other.evaluate('refresh()')
                expect(other.locator('#account-label')).to_have_text('等待导入学校账号')
                assert not record(other).get('token')
                other.close()

                # Hide the document and advance timers: no background status/frame requests.
                page.clock.install()
                page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true}); document.dispatchEvent(new Event('visibilitychange'))")
                before = len(requests)
                page.clock.fast_forward(90000)
                assert len(requests) == before
                page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>false}); document.dispatchEvent(new Event('visibilitychange'))")
                page.wait_for_function('!refreshInFlight')
                assert len(requests) > before
                page.screenshot(path='/tmp/goodnight-session-desktop.png', full_page=True)
                context.close()

                # Fault injection exercises the fallback code in a real browser.
                failures = {
                    'disabled': "Object.defineProperty(window,'indexedDB',{get(){throw new Error('disabled')}})",
                    'blocked': "Object.defineProperty(window,'indexedDB',{value:{open(){return {}}}})",
                    'transaction': "IDBDatabase.prototype.transaction=function(){throw new Error('transaction disabled')}",
                }
                for kind, script in failures.items():
                    context = context_for(kind, script, mobile=True)
                    page = open_page(context)
                    seed(page, kind)
                    stored(page, 'localStorage')
                    backend.expire(kind)
                    page.evaluate('refresh()')
                    expect(page.locator(MESSAGE)).to_contain_text('学校登录已恢复', timeout=10000)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path='/tmp/goodnight-session-mobile.png', full_page=True)
                    context.close()

                unavailable = failures['disabled'] + ";Object.defineProperty(window,'localStorage',{get(){throw new Error('disabled')}})"
                context = context_for('unavailable', unavailable)
                page = open_page(context)
                seed(page, 'unavailable')
                expect(page.locator(MESSAGE)).to_contain_text('无法保存登录')
                expect(page.locator('#account-label')).to_contain_text(SNAPSHOT['student_id'])
                expect(page.locator('#retry-session-restore')).to_be_visible()
                before = sum(owner == 'unavailable' and path == '/api/session/package' for owner, path in requests)
                page.evaluate('refresh()')
                assert sum(owner == 'unavailable' and path == '/api/session/package' for owner, path in requests) == before
                page.locator('#retry-session-restore').click()
                expect(page.locator(MESSAGE)).to_contain_text('无法保存登录')
                context.close()

                assert not errors, errors
                print('Session UI PASS: IndexedDB, workspace expiry, backend restart, unchanged expiry, '
                      'transient retry, multi-tab revoke/clear, hidden polling, localStorage fallback '
                      '(disabled/blocked/failed IndexedDB), unavailable storage, mobile; real school requests=0')
        finally:
            browser.close()
            backend.close()


if __name__ == '__main__':
    main()
