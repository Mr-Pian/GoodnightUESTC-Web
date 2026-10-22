"""Isolated server-side Chromium login; credentials remain in memory only."""
import re
import math
import queue
import threading
import time
from core import ORIGIN, CAS, browser_allowed, verify_session
from urllib.parse import urlsplit
from network import NETWORK

LOGIN_URL = CAS + '/login?service=https%3A%2F%2Fjzsz.uestc.edu.cn%2F'
ENTRY_URL = ORIGIN + '/daily-checkin/checkLogin?appId=294'


class BrowserLogin:
    def __init__(self, store, *, slots=None):
        self.store = store
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.state = {'state': 'idle', 'message': '尚未开始登录'}
        self.thread = None
        self.started_at = None
        self.slots = slots
        self.owns_slot = False
        self.commands = queue.Queue(maxsize=16)
        self.frame = None

    def status(self):
        with self.lock:
            result = dict(self.state)
            if self.started_at is not None:
                result['elapsed'] = round(time.monotonic()-self.started_at, 1)
            return result

    def update(self, state, message):
        with self.lock:
            self.state = {'state': state, 'message': message}

    def start(self, student, password):
        if not re.fullmatch(r'\d{8,20}', student) or not 1 <= len(password) <= 256:
            raise ValueError('请输入有效学号和密码。')
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('登录窗口已打开，请先完成或取消当前登录。')
            if self.slots and not self.slots.acquire(blocking=False):
                raise RuntimeError('登录服务繁忙，请稍后重试。')
            self.owns_slot = self.slots is not None
            self.cancelled.clear()
            self.frame = None
            self.started_at = time.monotonic()
            self.state = {'state': 'opening', 'message': '正在打开独立学校登录页面…'}
            self.thread = threading.Thread(target=self.run, args=(student, password), daemon=True)
            try:
                self.thread.start()
            except Exception:
                if self.owns_slot:
                    self.slots.release()
                    self.owns_slot = False
                raise

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            self.frame = None

    def screenshot(self):
        with self.lock:
            return self.frame

    def command(self, value):
        """Only bounded user input; never accept a URL, selector, or JavaScript."""
        if not self.thread or not self.thread.is_alive() or self.cancelled.is_set():
            raise ValueError('登录页面尚未就绪或已经关闭。')
        if not isinstance(value, dict):
            raise ValueError('无效的登录操作。')
        kind = value.get('kind')
        if kind in ('click', 'drag'):
            keys = ('x', 'y', 'to_x', 'to_y') if kind == 'drag' else ('x', 'y')
            for key in keys:
                v = value.get(key)
                limit = 1000 if key.endswith('x') else 760
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v < limit:
                    raise ValueError('点击位置无效。')
            action = {key: value[key] for key in keys}
        elif kind == 'text' and isinstance(value.get('text'), str) and 1 <= len(value['text']) <= 256:
            action = {'text': value['text']}
        elif kind == 'key' and value.get('key') in ('Tab', 'Enter', 'Backspace', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Control+A'):
            action = {'key': value['key']}
        elif kind == 'scroll' and value.get('delta') in (-500, 500):
            action = {'delta': value['delta']}
        else:
            raise ValueError('不支持的登录操作。')
        try:
            self.commands.put_nowait((time.monotonic(), kind, action))
        except queue.Full:
            raise ValueError('操作过快，请稍后重试。') from None

    def interact(self, page):
        while not self.commands.empty():
            created, kind, action = self.commands.get_nowait()
            if self.cancelled.is_set() or time.monotonic() - created > 5:
                continue
            if kind == 'click':
                page.mouse.click(action['x'], action['y'])
            elif kind == 'drag':
                page.mouse.move(action['x'], action['y'])
                page.mouse.down()
                page.mouse.move(action['to_x'], action['to_y'], steps=15)
                page.mouse.up()
            elif kind == 'text':
                page.keyboard.insert_text(action['text'])
            elif kind == 'key':
                page.keyboard.press(action['key'])
            elif kind == 'scroll':
                page.mouse.wheel(0, action['delta'])
        frame = page.screenshot(type='jpeg', quality=65, timeout=3000, animations='disabled')
        with self.lock:
            if not self.cancelled.is_set():
                self.frame = frame

    def run(self, student, password):
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, timeout=15000,
                                            **NETWORK.browser_options('idas.uestc.edu.cn'))
                try:
                    context = browser.new_context(service_workers='block', viewport={'width': 1000, 'height': 760},
                                                  accept_downloads=False)
                    previous = self.store.session()
                    if previous and previous['student_id'] == student:
                        context.add_cookies(previous['cookies'])
                    # Protect even a manually navigated business page from attendance writes.
                    context.route('**/*', lambda route: route.continue_() if browser_allowed(
                        route.request.method, route.request.url) else route.abort('blockedbyclient'))
                    context.route_web_socket('**/*', lambda socket: socket.close())
                    page = context.new_page()
                    user_agent = page.evaluate('navigator.userAgent')
                    page.goto(LOGIN_URL, wait_until='commit', timeout=20000)
                    try:
                        if urlsplit(page.url).hostname == 'jzsz.uestc.edu.cn':
                            raise ValueError('Existing session already redirected to portal')
                        account_input = page.locator('input#username:visible').first
                        password_input = page.locator('input#password:visible').first
                        account_input.fill(student, timeout=10000)
                        password_input.click(timeout=5000)
                        # School marks this field readonly to suppress browser autofill.
                        # Removing the presentation attribute does not skip authentication.
                        password_input.evaluate("element => element.removeAttribute('readonly')")
                        password_input.fill(password, timeout=5000)
                        password = ''
                        login = page.locator('#login_submit:visible').first
                        if login.count() and login.is_visible():
                            login.click(timeout=5000, no_wait_after=True)
                    except Exception:
                        # Changed selectors or manual authentication: leave the visible UI available.
                        password = ''
                    self.update('waiting', '请在下方学校页面完成验证码或二次验证。')
                    deadline = time.monotonic() + 600
                    entered = False
                    last_probe = 0
                    while time.monotonic() < deadline and not self.cancelled.is_set():
                        if page.is_closed():
                            self.update('cancelled', '登录窗口已关闭，可重新开始。')
                            return
                        self.interact(page)
                        cookies = context.cookies([ORIGIN, CAS])
                        if (urlsplit(page.url).hostname == 'jzsz.uestc.edu.cn'
                                and any(c['name'] == 'SESSION' and c['domain'].lstrip('.') == 'jzsz.uestc.edu.cn' for c in cookies)):
                            if not entered:
                                entered = True
                                try:
                                    self.update('verifying', '正在打开智慧学工签到应用…')
                                    page.goto(ENTRY_URL, wait_until='commit', timeout=10000)
                                except Exception:
                                    pass
                            if time.monotonic() - last_probe >= 2:
                                last_probe = time.monotonic()
                                snapshot = {'student_id': student, 'cookies': context.cookies([ORIGIN, CAS]),
                                            'user_agent': user_agent, 'saved_at': time.time()}
                                try:
                                    self.update('verifying', '正在验证学校会话并保存 Cookie…')
                                    verify_session(snapshot)
                                except Exception:
                                    self.update('waiting', '已获取登录 Cookie，等待智慧学工授权完成；请按学校页面提示操作。')
                                else:
                                    if self.cancelled.is_set():
                                        self.update('cancelled', '已取消登录，会话未保存。')
                                        return
                                    self.store.save_session(snapshot)
                                    self.update('success', '登录成功，可按页面设置将加密会话保存到此浏览器。')
                                    return
                        page.wait_for_timeout(1000)
                    self.update('cancelled', '已取消登录。' if self.cancelled.is_set() else '登录已超时，请重试。')
                finally:
                    browser.close()
        except Exception:
            self.update('error', '登录未能完成，请检查浏览器运行环境和学校网络后重试。')
        finally:
            password = ''
            with self.lock:
                self.frame = None
                while not self.commands.empty():
                    self.commands.get_nowait()
                if self.owns_slot:
                    self.slots.release()
                    self.owns_slot = False
