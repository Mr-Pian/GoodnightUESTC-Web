"""School session validation, coordinates and read-only diagnostic transport."""
import http.cookiejar
from pathlib import Path
import sys
import urllib.parse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'vendor'))
from moonuestc.client import School, ORIGIN, CAS
from moonuestc.coordinates import to_gcj02
from moonuestc.errors import ToolError, AuthRequired
from moonuestc.policy import check_fence, now, valid_today, window
from moonuestc.transport import Transport, NoRedirect
from network import RoutingOpener

READ_PATHS = frozenset({
    '/', '/api/isLoginFailure', '/daily-checkin/checkLogin',
    '/daily-checkin-h5/index.html',
    '/daily-checkin/api/student-checkin-record/today',
    '/daily-checkin/api/checkin-schedule/effective',
    '/daily-checkin/api/checkin-location/available-locations',
    '/daily-checkin/api/map/reverse',
})


def allowed_read(method, url):
    u = urllib.parse.urlsplit(url)
    try:
        return (method.upper() == 'GET' and u.scheme == 'https'
                and u.hostname == 'jzsz.uestc.edu.cn' and u.port in (None, 443)
                and not u.username and not u.password and u.path in READ_PATHS)
    except ValueError:
        return False


def browser_allowed(method, url):
    """Authentication forms are allowed; school business writes are blocked."""
    u = urllib.parse.urlsplit(url)
    if u.scheme in ('about', 'data', 'blob'):
        return True
    if u.scheme != 'https' or u.hostname not in ('idas.uestc.edu.cn', 'jzsz.uestc.edu.cn'):
        return False
    if u.username or u.password or u.port not in (None, 443):
        return False
    if u.hostname == 'idas.uestc.edu.cn':
        return u.path.startswith('/authserver/')
    if method.upper() not in ('GET', 'HEAD'):
        return False
    if u.path.startswith('/daily-checkin/api/'):
        return u.path in READ_PATHS
    # Static app resources and portal login routes. Explicitly deny record actions.
    if any(s in u.path.lower() for s in ('/checkin-record/checkin', '/report', '/submit', '/save', '/delete')):
        return False
    return True


def normalize_location(value):
    if not isinstance(value, dict):
        raise ToolError('invalid_location')
    try:
        lat, lng = float(value['latitude']), float(value['longitude'])
        system = value.get('coordinate_system', 'wgs84')
        gcj_lat, gcj_lng, method = to_gcj02(lat, lng, system)
    except (KeyError, ValueError, TypeError):
        raise ToolError('invalid_location') from None
    name = str(value.get('name', '手动选点')).strip()[:80] or '手动选点'
    return {'name': name, 'latitude': lat, 'longitude': lng,
            'coordinate_system': system, 'gcj02': {'latitude': gcj_lat, 'longitude': gcj_lng},
            'conversion': method}


class ReadOnlyTransport(Transport):
    def __init__(self, snapshot, timeout=8):
        self.snapshot = snapshot
        self.user_agent = snapshot['user_agent']
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        for item in snapshot['cookies']:
            domain = item['domain']
            if domain.lstrip('.') not in ('idas.uestc.edu.cn', 'jzsz.uestc.edu.cn', 'uestc.edu.cn'):
                continue
            expires = item.get('expires', -1)
            self.jar.set_cookie(http.cookiejar.Cookie(
                version=0, name=item['name'], value=item['value'], port=None, port_specified=False,
                domain=domain, domain_specified=domain.startswith('.'), domain_initial_dot=domain.startswith('.'),
                path=item.get('path', '/'), path_specified=True, secure=item.get('secure', True),
                expires=int(expires) if expires > 0 else None, discard=expires <= 0,
                comment=None, comment_url=None, rest={'HttpOnly': None} if item.get('httpOnly') else {}))
        self.opener = RoutingOpener(self.jar)

    def request(self, method, url, *, stage, headers=None, form=None, payload=None):
        if method == 'GET' and urllib.parse.urlsplit(url).hostname == 'idas.uestc.edu.cn':
            raise AuthRequired('session_rejected', 'session')
        if not allowed_read(method, url) or form is not None or payload is not None:
            raise ToolError('write_blocked', 'readonly_guard')
        try:
            return super().request(method, url, stage=stage, headers=headers)
        finally:
            self.save()

    def save(self):
        # Keep Set-Cookie rotations/deletions in the caller's isolated snapshot.
        original = {(c['name'], c['domain'], c.get('path', '/')): c for c in self.snapshot['cookies']}
        cookies = []
        for cookie in self.jar:
            if cookie.is_expired():
                continue
            value = dict(original.get((cookie.name, cookie.domain, cookie.path), {}))
            value.update(name=cookie.name, value=cookie.value, domain=cookie.domain, path=cookie.path,
                         expires=cookie.expires if cookie.expires is not None else -1,
                         secure=cookie.secure, httpOnly=cookie.has_nonstandard_attr('HttpOnly'))
            cookies.append(value)
        self.snapshot['cookies'] = cookies


class ReadOnlySchool(School):
    def submit(self, payload):
        raise ToolError('write_blocked', 'readonly_guard')


def read_today(school):
    try:
        return school.today()
    except AuthRequired:
        return school.enter()


def verify_session(snapshot):
    """Validate saved login, reopening the school app with read-only GETs if needed."""
    school = ReadOnlySchool(ReadOnlyTransport(snapshot, timeout=6), {}, lambda: None, emit=lambda *a, **k: None)
    data = read_today(school)
    if not isinstance(data, dict):
        raise ToolError('invalid_record', 'today')


def online_test(snapshot, location, *, progress=lambda message: None):
    school = ReadOnlySchool(ReadOnlyTransport(snapshot), {}, lambda: None, emit=lambda *a, **k: None)
    progress('正在读取学校当天记录…')
    record = read_today(school)
    instant = now()
    status = valid_today(record, instant)
    progress('正在读取允许签到的时段…')
    schedule = window(school.schedule(), instant)
    progress('正在查询位置范围…')
    fences = school.locations()
    result = {'mode': 'online_readonly', 'submitted': False, 'schoolWrites': 0,
              'businessDate': instant.date().isoformat(), 'recordStatus': status,
              'window': schedule, 'fenceCount': len(fences) if isinstance(fences, list) else 0,
              'insideFence': None}
    if location:
        point = normalize_location(location)['gcj02']
        try:
            check_fence(point, fences)
            result['insideFence'] = True
        except ToolError as error:
            if error.reason != 'outside_allowed_location':
                raise
            result['insideFence'] = False
        result['location'] = point
    return result


def offline_test(location):
    normalized = normalize_location(location) if location else None
    blocked = []
    transport = ReadOnlyTransport({'user_agent': 'LocalOfflineTest/1.0', 'cookies': []})
    for method, path in [('POST', '/student-checkin-record/checkin'),
                         ('POST', '/student-checkin-record/report'),
                         ('GET', '/student-checkin-record/checkin')]:
        try:
            transport.request(method, ORIGIN + '/daily-checkin/api' + path, stage='guard_test')
        except ToolError as error:
            if error.reason != 'write_blocked':
                raise
            blocked.append(method + ' ' + path)
        else:
            raise ToolError('guard_test_failed')
    return {'mode': 'offline', 'passed': True, 'schoolRequests': 0, 'schoolWrites': 0,
            'submitted': False, 'blockedRequests': blocked, 'location': normalized}
