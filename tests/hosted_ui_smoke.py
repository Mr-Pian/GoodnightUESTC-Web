"""Browser smoke test with a synthetic school page; no real school requests."""
import base64
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlsplit
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_app
from browser_login import BrowserLogin
from core import now
from hosted import HostedSettings
from playwright.sync_api import sync_playwright, expect
from waitress import create_server
from web_support import SNAPSHOT, POINT, BrowserContexts, browser_options

BASE = 'https://team.example.com'
SECRET = 'synthetic-gateway-secret-' * 3
TILE = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


def main():
    errors = []
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
        app = create_app(directory, hosted=HostedSettings(
            BASE, SECRET, tianditu_key='synthetic-tianditu-browser-key'))
        server = create_server(app, host='127.0.0.1', port=0, threads=8)
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        browser = p.chromium.launch(headless=True, **browser_options())
        contexts = BrowserContexts(browser)
        try:
            school_context = contexts.new(viewport={'width': 1000, 'height': 760})
            school_context.route('**/*', lambda route: route.abort())
            school = school_context.new_page()
            school.set_content('''<!doctype html><html><body style="margin:0;background:#f4f7f9">
                <h1 style="padding:20px">Synthetic school verification</h1>
                <form style="position:absolute;left:50px;top:100px" onsubmit="event.preventDefault();document.querySelector('#done').textContent='verified'">
                <label>Code <input id="code" autocomplete="off"></label><button>Confirm</button></form><p id="done"></p>
                </body></html>''')
            school_frame = school.screenshot(type='jpeg')

            map_network = {'online': False}
            tile_urls = []

            def proxy(route):
                parsed = urlsplit(route.request.url)
                if parsed.hostname in {f't{i}.tianditu.gov.cn' for i in range(8)}:
                    tile_urls.append(route.request.url)
                    if map_network['online']:
                        route.fulfill(status=200, content_type='image/png', body=TILE)
                    else:
                        route.abort('connectionclosed')
                    return
                if not route.request.url.startswith(BASE + '/'):
                    route.abort()
                    return
                response = route.fetch(
                    url=route.request.url.replace(BASE, 'http://127.0.0.1:'+str(server.effective_port)),
                    headers={**route.request.headers, 'host': 'team.example.com',
                             'X-Team-Gateway': SECRET, 'X-Client-IP': '192.0.2.1'},
                    timeout=10000, max_retries=0)
                route.fulfill(response=response)

            a = contexts.new(viewport={'width': 1440, 'height': 1200})
            b = contexts.new(viewport={'width': 390, 'height': 844})
            a.route('**/*', proxy)
            b.route('**/*', proxy)
            page, other = a.new_page(), b.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            other.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(BASE, wait_until='networkidle')
            other.goto(BASE, wait_until='networkidle')
            expect(page.locator('.mode')).to_contain_text('学校账号登录')
            expect(other.locator('#account-label')).to_have_text('等待导入学校账号')
            expect(page.locator('#map-provider')).to_have_value('tianditu')
            expect(page.locator('#map-status')).to_contain_text('天地图底图部分内容加载失败')
            assert any('/vec_w/wmts?' in url for url in tile_urls)
            assert any('/cva_w/wmts?' in url for url in tile_urls)
            map_network['online'] = True
            page.locator('#retry-map').click()
            page.wait_for_function('() => document.querySelectorAll("#map .leaflet-tile-loaded").length > 0')
            expect(page.locator('#map-status')).to_be_hidden()
            expect(page.locator('#map-source')).to_contain_text('天地图 · CGCS2000')

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

                page.locator('.leaflet-container').wait_for()
                assert abs(page.evaluate('() => map.getCenter().lat') - 30.6752) < .001
                # A known GCJ-02 point must display at its approximate WGS-84 location.
                page.locator('#coordinate-system').select_option('gcj02')
                page.locator('#latitude').fill('30.75470828983461')
                page.locator('#longitude').fill('103.93284383329953')
                page.locator('#place-name').click()
                page.wait_for_function('() => marker && Math.abs(marker.getLatLng().lat - 30.757413) < 0.000001')
                assert abs(page.evaluate('() => marker.getLatLng().lng') - 103.930739) < .000001
                expect(page.locator('#latitude')).to_have_value('30.75470828983461')
                page.locator('#campus-qingshuihe').click()
                expect(page.locator('#campus-qingshuihe')).to_have_attribute('aria-pressed', 'true')
                page.wait_for_function('() => Math.abs(map.getCenter().lng - 103.9313) < .001')
                page.locator('#campus-shahe').click()
                page.wait_for_function('() => Math.abs(map.getCenter().lat - 30.6752) < .001')
                page.locator('#map').click(position={'x': 250, 'y': 160})
                assert page.locator('#latitude').input_value()
                expect(page.locator('#coordinate-system')).to_have_value('wgs84')
                page.locator('#place-name').fill('地图选点测试')
                page.locator('#save-location').click()
                expect(page.locator('#converted')).to_contain_text('GCJ-02：')
                expect(page.locator('#prepare-checkin')).to_be_enabled()
                selected = page.evaluate('() => ({location: currentLocation(), marker: marker.getLatLng()})')
                page.locator('#map-provider').select_option('osm')
                expect(page.locator('#map-status')).to_contain_text('OpenStreetMap底图部分内容加载失败')
                assert page.evaluate('() => ({location: currentLocation(), marker: marker.getLatLng()})') == selected
                page.locator('#map-provider').select_option('tianditu')
                page.wait_for_function('() => document.querySelectorAll("#map .leaflet-tile-loaded").length > 0')
                expect(page.locator('#map-status')).to_be_hidden()
                assert page.evaluate('() => ({location: currentLocation(), marker: marker.getLatLng()})') == selected
                expect(page.locator('#prepare-checkin')).to_be_enabled()

                page.locator('#offline').click()
                expect(page.locator('#result-title')).to_have_text('离线自检通过')
                diagnostic = page.locator('#result-json').text_content()
                assert '"schoolRequests": 0' in diagnostic
                assert '"schoolWrites": 0' in diagnostic

                preview = {'businessDate': now().date().isoformat(),
                           'name': POINT['name'], 'address': '合成测试地址',
                           'latitude': POINT['latitude'], 'longitude': POINT['longitude'],
                           'window': {'start': '21:00', 'end': '23:30'}}
                with patch('app.prepare_checkin', return_value=preview), patch(
                        'app.submit_checkin', side_effect=[
                            {'confirmed': False, 'submitted': True, 'result': 'rejected',
                             'retryAllowed': True, 'apiCode': 4001, 'httpStatus': 200,
                             'schoolMessage': '合成测试：请重新检查'},
                            {'confirmed': True, 'submitted': True, 'result': 'confirmed'}]) as submit:
                    page.locator('#prepare-checkin').click()
                    expect(page.locator('#checkin-dialog')).to_be_visible()
                    expect(page.locator('#submit-checkin')).to_be_disabled()
                    page.locator('#confirm-location').check()
                    page.locator('#submit-checkin').click()
                    expect(page.locator('#checkin-state')).to_have_text('学校拒绝提交，可重新检查')
                    expect(page.locator('#checkin-message')).to_contain_text('业务码 4001')
                    expect(page.locator('#checkin-message')).to_contain_text('合成测试：请重新检查')
                    expect(page.locator('#checkin-dialog')).not_to_be_visible()
                    submit.assert_called_once()
                    page.locator('#prepare-checkin').click()
                    expect(page.locator('#checkin-dialog')).to_be_visible()
                    expect(page.locator('#submit-checkin')).to_be_disabled()
                    page.locator('#confirm-location').check()
                    page.locator('#submit-checkin').click()
                    expect(page.locator('#checkin-state')).to_have_text('签到已确认')
                    assert submit.call_count == 2
                page.screenshot(path='/tmp/goodnight-hosted-desktop.png', full_page=True)
                other.screenshot(path='/tmp/goodnight-hosted-mobile.png', full_page=True)
                assert other.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                print('Hosted UI PASS: isolated users, remote input, password clearing, Tianditu vector/labels, '
                      'tile failure/retry, basemap switch without coordinate changes, GCJ-02 display, campus/map selection, '
                      'offline test, explicit rejection and manually confirmed retry, mobile; real school requests=0')
        finally:
            try:
                contexts.close_all()
            finally:
                browser.close()
                server.close()
                app.extensions['workspaces'].close()
                worker.join(timeout=3)


if __name__ == '__main__':
    main()
