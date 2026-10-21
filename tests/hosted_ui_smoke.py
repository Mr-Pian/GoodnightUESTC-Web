"""Browser smoke test with a synthetic school page; no real school requests."""
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_app
from browser_login import BrowserLogin
from hosted import HostedSettings
from playwright.sync_api import sync_playwright, expect
from waitress import create_server
from test_readonly import SNAPSHOT

BASE = 'https://team.example.com'
SECRET = 'synthetic-gateway-secret-' * 3


def main():
    errors = []
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
        app = create_app(directory, hosted=HostedSettings(BASE, SECRET))
        server = create_server(app, host='127.0.0.1', port=0, threads=8)
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        # Override only the test browser binary; production uses bundled Chromium.
        browser = p.chromium.launch(channel='msedge', headless=True)
        try:
            school_context = browser.new_context(viewport={'width': 1000, 'height': 760})
            school_context.route('**/*', lambda route: route.abort())
            school = school_context.new_page()
            school.set_content('''<!doctype html><html><body style="margin:0;background:#f4f7f9">
                <h1 style="padding:20px">Synthetic school verification</h1>
                <form style="position:absolute;left:50px;top:100px" onsubmit="event.preventDefault();document.querySelector('#done').textContent='verified'">
                <label>Code <input id="code" autocomplete="off"></label><button>Confirm</button></form><p id="done"></p>
                </body></html>''')
            school_frame = school.screenshot(type='jpeg')

            def route_for(owner):
                def proxy(route):
                    if not route.request.url.startswith(BASE + '/'):
                        route.abort()
                        return
                    response = route.fetch(
                        url=route.request.url.replace(BASE, 'http://127.0.0.1:'+str(server.effective_port)),
                        headers={**route.request.headers, 'host': 'team.example.com',
                                 'X-Team-Gateway': SECRET, 'X-Team-User': owner})
                    route.fulfill(response=response)
                return proxy

            a = browser.new_context(viewport={'width': 1440, 'height': 1200})
            b = browser.new_context(viewport={'width': 390, 'height': 844})
            a.route('**/*', route_for('alice'))
            b.route('**/*', route_for('bob'))
            page, other = a.new_page(), b.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            other.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(BASE, wait_until='networkidle')
            other.goto(BASE, wait_until='networkidle')
            expect(page.locator('.mode')).to_contain_text('测试组专用')
            expect(other.locator('#account-label')).to_have_text('等待导入学校账号')

            started = []
            def begin(manager, student, password):
                assert student == SNAPSHOT['student_id']
                assert password == 'synthetic-password'
                manager.thread = MagicMock()
                manager.thread.is_alive.return_value = True
                manager.frame = school_frame
                manager.update('waiting', 'Synthetic school page')
                started.append(manager)

            with patch.object(BrowserLogin, 'start', autospec=True, side_effect=begin):
                page.locator('#student-id').fill(SNAPSHOT['student_id'])
                page.locator('#password').fill('synthetic-password')
                page.locator('#login-button').click()
                expect(page.locator('#password')).to_have_value('')
                image = page.locator('#remote-login-image')
                expect(image).to_be_visible()
                login = started[0]

                def drain():
                    for _ in range(30):
                        if not login.commands.empty():
                            login.interact(school)
                            return
                        page.wait_for_timeout(100)
                    raise AssertionError('Remote input did not reach the owning login browser')

                box = image.bounding_box()
                image.click(position={'x': 160*box['width']/1000, 'y': 110*box['height']/760})
                drain()
                page.locator('#remote-text').fill('synthetic-code')
                page.locator('#remote-text-form button').click()
                drain()
                expect(school.locator('#code')).to_have_value('synthetic-code')
                expect(page.locator('#remote-text')).to_have_value('')
                page.locator('[data-key="Enter"]').click()
                drain()
                expect(school.locator('#done')).to_have_text('verified')
                expect(other.locator('.remote-login')).not_to_be_visible()
                assert other.evaluate("fetch('/api/login/frame').then(r=>r.json()).then(r=>r.image)") is None
                login.store.save_session(SNAPSHOT)
                login.thread.is_alive.return_value = False
                login.update('success', 'Synthetic session imported')
                expect(page.locator('.remote-login')).not_to_be_visible()
                expect(page.locator('#account-label')).to_contain_text(SNAPSHOT['student_id'])
                expect(other.locator('#account-label')).not_to_contain_text(SNAPSHOT['student_id'])
                page.locator('#offline').click()
                expect(page.locator('#result-title')).to_have_text('离线自检通过')
                page.screenshot(path='/tmp/goodnight-hosted-desktop.png', full_page=True)
                other.screenshot(path='/tmp/goodnight-hosted-mobile.png', full_page=True)
                assert other.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                print('Hosted UI PASS: isolated users, remote frames/click/text/key, password clearing, offline test, mobile; real school requests=0')
        finally:
            browser.close()
            server.close()
            app.extensions['workspaces'].close()
            worker.join(timeout=3)


if __name__ == '__main__':
    main()
