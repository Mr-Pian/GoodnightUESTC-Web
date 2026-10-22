import base64
import hashlib
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit
from flask import Flask, g, jsonify, request, session, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.local import LocalProxy
from core import ROOT, ToolError, offline_test, online_test, verify_session, now
from checkin import Attempts, prepare_checkin, submit_checkin
from hosted import WorkspaceRegistry, RateLimits
from session_packages import DEVICE_COOKIE, PackageError, SessionPackages

MESSAGES = {
    'write_blocked': '测试模式已拦截学校写入请求。',
    'invalid_location': '请输入有效的经纬度。',
    'invalid_configured_coordinates': '经纬度超出有效范围或不是有限数字。',
    'coordinate_system_mismatch': '请选择 WGS-84 或 GCJ-02。',
    'session_rejected': '学校会话已失效，请重新登录。',
    'portal_session_rejected': '学校会话已失效，请重新登录。',
    'micro_app_unauthorized': '智慧学工打卡应用未授权，请重新登录并完成页面提示。',
    'network_error': '无法连接学校网络，请检查连接后重试。',
    'timeout': '学校请求超时，请稍后重试。',
    'business_date_mismatch': '学校返回的记录日期不是今天。',
    'invalid_schedule': '学校未返回当前有效打卡时段。',
    'location_required': '请先选择并保存实际位置。',
    'outside_allowed_location': '所选位置不在学校允许范围内，无法提交。',
    'outside_checkin_window': '当前不在学校允许的签到时段内。',
    'already_recorded': '学校当天已有签到、报告或豁免记录，无需重复提交。',
    'business_date_changed': '日期已变化，请重新检查学校记录。',
}


def create_app(data_dir=None, *, hosted):
    app = Flask(__name__, static_folder=str(ROOT / 'static'))
    app.secret_key = secrets.token_bytes(32)
    app.config.update(MAX_CONTENT_LENGTH=98304, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=True,
                      SESSION_COOKIE_NAME='__Host-uestc')
    directory = Path(data_dir or '/data')
    directory.mkdir(parents=True, exist_ok=True)
    packages = SessionPackages(directory, hosted)
    registry, limits = WorkspaceRegistry(hosted), RateLimits()
    app.extensions['session_packages'] = packages
    app.extensions.update(workspaces=registry, hosted_settings=hosted)
    attempts = Attempts(directory)
    store = LocalProxy(lambda: g.workspace.store)
    login = LocalProxy(lambda: g.workspace.login)
    test_lock = LocalProxy(lambda: g.workspace.test_lock)
    operation = LocalProxy(lambda: g.workspace.operation)
    pending = LocalProxy(lambda: g.workspace.pending)
    pending_lock = LocalProxy(lambda: g.workspace.pending_lock)

    @app.before_request
    def protect():
        if request.path == '/healthz' and request.method in ('GET', 'HEAD'):
            return None
        if request.path != '/api/session/restore' and (request.content_length or 0) > 16384:
            return jsonify(error='请求内容过大。'), 413
        owner = request.headers.get('X-Team-User', '')
        gateway = request.headers.get('X-Team-Gateway', '')
        if (not secrets.compare_digest(gateway.encode(), hosted.gateway_secret.encode())
                or not owner or len(owner) > 128):
            return jsonify(error='请通过测试组访问入口登录。'), 403
        if request.host != urlsplit(hosted.public_origin).netloc:
            return jsonify(error='请求地址不匹配。'), 403
        categories = [('all', 240, 60)]
        if request.method == 'POST':
            categories.append(('input' if request.path == '/api/login/input' else 'write',
                               180 if request.path == '/api/login/input' else 30, 60))
        if request.path == '/api/login' and request.method == 'POST':
            categories.append(('login', 3, 300))
        if request.path == '/api/session/restore' and request.method == 'POST':
            categories.append(('restore', 5, 300))
        if not all(limits.allow(owner, *category) for category in categories):
            response = jsonify(error='请求过于频繁，请稍后重试。')
            response.headers['Retry-After'] = '300' if request.path == '/api/login' else '60'
            return response, 429
        sid, g.workspace = registry.acquire(session.get('workspace'), owner)
        if g.workspace is None:
            return jsonify(error='工作台数量已达上限，请稍后重试。'), 503
        if sid != session.get('workspace'):
            session.clear()
            session['workspace'] = sid
        g.owner = owner
        device = request.cookies.get(DEVICE_COOKIE, '')
        g.new_device = not re.fullmatch(r'[A-Za-z0-9_-]{43}', device)
        g.device = secrets.token_urlsafe(32) if g.new_device else device
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('Origin') != hosted.public_origin:
                return jsonify(error='请求来源不匹配，请从工作台页面操作。'), 403
            token = g.workspace.csrf
            if not token or not secrets.compare_digest(request.headers.get('X-CSRF-Token', '').encode(), token.encode()):
                return jsonify(error='页面会话已更新，请刷新页面。'), 403

    @app.teardown_request
    def release_workspace(error):
        entry = g.pop('workspace', None)
        if entry:
            registry.release(entry)

    @app.after_request
    def headers(response):
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'self'; object-src 'none'")
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Cache-Control'] = 'no-store'
        if g.get('device') and (g.new_device or g.get('renew_device')):
            response.set_cookie(DEVICE_COOKIE, g.device, max_age=hosted.package_seconds,
                                secure=True, httponly=True, samesite='Strict', path='/')
        return response

    @app.errorhandler(PackageError)
    def package_error(error):
        messages = {
            'saved_session_expired': '此浏览器保存的登录已到期，请重新登录。',
            'saved_session_unavailable': '保存的登录已被清除或撤销，请重新登录。',
            'saved_session_too_large': '学校会话过大，无法保存到浏览器；本次登录仍可使用。',
            'saved_session_capacity': '已保存的设备数量已达上限，本次登录仍可使用。',
        }
        return jsonify(error=messages.get(error.reason, '无法恢复此浏览器保存的登录，请重新登录。'),
                       reason=error.reason), 400

    def school_call(function, snapshot, *args, **kwargs):
        try:
            if store.package and not packages.current(store.package, g.owner, g.device):
                raise ToolError('session_rejected')
            return function(snapshot, *args, **kwargs)
        except ToolError as error:
            if error.reason in ('session_rejected', 'portal_session_rejected', 'micro_app_unauthorized'):
                packages.revoke(g.owner, g.device)
                store.forget()
                with pending_lock:
                    pending.clear()
            raise
        finally:
            store.update_session(snapshot)

    @app.errorhandler(ToolError)
    def school_error(error):
        return jsonify(error=MESSAGES.get(error.reason, '检查未通过：' + error.reason),
                       reason=error.reason, stage=error.stage, submitted=False), 400

    @app.errorhandler(Exception)
    def unexpected(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        return jsonify(error='操作未完成，请重试或查看使用说明。', submitted=False), 500

    @app.get('/')
    def index():
        return send_from_directory(ROOT / 'static', 'index.html')

    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.get('/api/status')
    def status():
        saved = store.session()
        return jsonify(product='GoodNightUESTC', csrf=g.workspace.csrf,
                       mode='manual', hosted=True, schoolWritesEnabled=True,
                       account=({'student_id': saved['student_id'],
                                 'saved_at': saved['saved_at'], 'cookie_count': len(saved['cookies'])} if saved else None),
                       location=store.location(), login=login.status(), operation=operation.snapshot(),
                       persistence=({'owner': packages.tag('owner', g.owner), 'revision': store.revision,
                                     'max_age': hosted.package_seconds,
                                     'expires_at': store.package['expires'] if store.package else None}))

    @app.post('/api/session/package')
    def export_package():
        if not test_lock.acquire(blocking=False):
            return jsonify(error='正在执行操作，请稍后保存登录。'), 409
        try:
            if login.thread and login.thread.is_alive():
                return jsonify(error='请先完成学校登录。'), 409
            snapshot = store.session()
            if not snapshot:
                return jsonify(error='请先完成学校登录。'), 400
            token, grant = packages.export(snapshot, g.owner, g.device, store.package)
            store.package = grant
            g.renew_device = True
            return jsonify(ok=True, token=token, expires_at=grant['expires'], revision=store.revision)
        finally:
            test_lock.release()

    @app.post('/api/session/restore')
    def restore_package():
        if not test_lock.acquire(blocking=False):
            return jsonify(error='正在执行操作，请稍后恢复登录。'), 409
        try:
            if store.session() or (login.thread and login.thread.is_alive()):
                return jsonify(error='当前已有登录或正在登录，请先清除后再恢复。'), 409
            data = request.get_json()
            snapshot, grant = packages.restore(data.get('token') if isinstance(data, dict) else None, g.owner, g.device)
            # One bounded read-only check; no browser, credential POST or submission.
            school_call(verify_session, snapshot)
            if not packages.current(grant, g.owner, g.device):
                raise PackageError('saved_session_unavailable')
            store.save_session(snapshot)
            store.package = grant
            g.renew_device = True
            token, _ = packages.export(snapshot, g.owner, g.device, grant)
            return jsonify(ok=True, token=token, expires_at=grant['expires'], revision=store.revision)
        finally:
            test_lock.release()

    @app.post('/api/session/package/forget')
    def forget_package():
        if not test_lock.acquire(blocking=False):
            return jsonify(error='正在执行操作，请稍后清除保存的登录。'), 409
        try:
            packages.revoke(g.owner, g.device)
            store.package = None
            return jsonify(ok=True)
        finally:
            test_lock.release()

    @app.post('/api/login')
    def start_login():
        data = request.get_json()
        if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ('student_id', 'password')):
            return jsonify(error='请输入学号和密码。'), 400
        if not test_lock.acquire(blocking=False):
            return jsonify(error='正在执行检查，请稍后再登录。'), 409
        try:
            login.start(data['student_id'].strip(), data['password'])
            with pending_lock:
                pending.clear()
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except RuntimeError:
            return jsonify(error='登录服务繁忙，请稍后重试。'), 429
        finally:
            test_lock.release()
        return jsonify(ok=True, login=login.status()), 202

    @app.get('/api/login/frame')
    def login_frame():
        frame = login.screenshot()
        return jsonify(image='data:image/jpeg;base64,' + base64.b64encode(frame).decode() if frame else None)

    @app.post('/api/login/input')
    def login_input():
        try:
            login.command(request.get_json())
        except ValueError as error:
            return jsonify(error=str(error)), 400
        return jsonify(ok=True), 202

    @app.post('/api/login/cancel')
    def cancel_login():
        login.cancel()
        return jsonify(ok=True)

    @app.post('/api/session/forget')
    def forget():
        if not test_lock.acquire(blocking=False):
            return jsonify(error='正在执行操作，请稍后清除。'), 409
        try:
            if login.thread and login.thread.is_alive():
                return jsonify(error='请先取消登录，等待登录窗口关闭后再清除。'), 409
            packages.revoke(g.owner, g.device)
            store.forget()
            with pending_lock:
                pending.clear()
            return jsonify(ok=True)
        finally:
            test_lock.release()

    @app.post('/api/location')
    def location():
        if not test_lock.acquire(blocking=False):
            return jsonify(error='正在执行操作，请稍后修改位置。'), 409
        try:
            result = store.save_location(request.get_json())
            with pending_lock:
                pending.clear()
            return jsonify(ok=True, location=result)
        finally:
            test_lock.release()

    def binding(snapshot, location):
        return hashlib.sha256(json.dumps({'account': snapshot, 'location': location},
                                         sort_keys=True).encode()).hexdigest()

    @app.post('/api/checkin/prepare')
    def prepare():
        if not test_lock.acquire(blocking=False):
            return jsonify(error='另一项检查正在进行，请稍后重试。'), 409
        try:
            if login.thread and login.thread.is_alive():
                return jsonify(error='请先完成或取消登录。'), 409
            snapshot, point = store.session(), store.location()
            if not snapshot:
                return jsonify(error='请先登录本人学校账号。'), 400
            with operation.operation('prepare'):
                preview = school_call(prepare_checkin, snapshot, point, progress=operation.step)
            token = secrets.token_urlsafe(32)
            with pending_lock:
                for key in list(pending):
                    if pending[key]['expires'] < time.monotonic():
                        pending.pop(key)
                if len(pending) >= 32:
                    return jsonify(error='待确认操作过多，请稍后重试。'), 429
                pending[token] = {'owner': g.workspace.csrf, 'binding': binding(snapshot, point),
                                  'day': preview['businessDate'],
                                  'address': preview.get('address'),
                                  'expires': time.monotonic()+120}
            return jsonify(ok=True, confirmation=token, preview=preview, expires_in=120)
        finally:
            test_lock.release()

    @app.post('/api/checkin')
    def checkin():
        data = request.get_json()
        if (not isinstance(data, dict) or data.get('confirm') is not True
                or data.get('actual_location_confirmed') is not True
                or not isinstance(data.get('confirmation'), str)):
            return jsonify(error='请先预检查，并明确确认实际位置和本次提交。', submitted=False), 400
        if not test_lock.acquire(blocking=False):
            return jsonify(error='有任务正在执行，请勿重复点击。', submitted=False), 409
        try:
            if login.thread and login.thread.is_alive():
                return jsonify(error='请先完成或取消登录。', submitted=False), 409
            snapshot, point = store.session(), store.location()
            with pending_lock:
                ticket = pending.pop(data['confirmation'], None)
            if (not ticket or ticket['expires'] < time.monotonic()
                    or ticket['day'] != now().date().isoformat()
                    or ticket['owner'] != g.workspace.csrf or not snapshot or not point
                    or ticket['binding'] != binding(snapshot, point)):
                return jsonify(error='确认已过期或账号、位置已变化，请重新预检查。', submitted=False), 400
            with operation.operation('checkin'):
                result = school_call(submit_checkin, snapshot, point, attempts, address=ticket.get('address'), progress=operation.step)
            return jsonify(ok=True, result=result)
        finally:
            test_lock.release()

    @app.post('/api/test/offline')
    def offline():
        return jsonify(ok=True, result=offline_test(store.location()))

    @app.post('/api/test/online')
    def online():
        if not test_lock.acquire(blocking=False):
            return jsonify(error='检查正在进行中。'), 409
        try:
            if login.thread and login.thread.is_alive():
                return jsonify(error='请先完成或取消登录。'), 409
            snapshot = store.session()
            if not snapshot:
                return jsonify(error='请先完成学校登录。'), 400
            with operation.operation('online'):
                result = school_call(online_test, snapshot, store.location(), progress=operation.step)
            return jsonify(ok=True, result=result)
        finally:
            test_lock.release()

    return app
