"""Reverse-proxy contract smoke test; synthetic loopback traffic only."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from http.cookies import SimpleCookie
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from flask import request
from hosted import HostedSettings
from waitress import create_server
from web_support import POINT

AUTHORITY = 'team.example.com:60000'
ORIGIN = 'https://' + AUTHORITY
SECRET = 'synthetic-gateway-secret-' * 3


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory, hosted=HostedSettings(ORIGIN, SECRET))
        forwarded = []

        @app.before_request
        def inspect_proxy_headers():
            forwarded.append((request.headers.get('X-Client-IP'), request.headers.get('X-Team-User'),
                              request.headers.get('Authorization')))

        backend = create_server(app, host='127.0.0.1', port=0, threads=4)
        worker = threading.Thread(target=backend.run, daemon=True)
        worker.start()

        class Proxy(BaseHTTPRequestHandler):
            # A test fixture for the contract, not a production proxy/TLS server.
            def log_message(self, *args):
                pass

            def forward(self):
                if self.headers.get('Host') != AUTHORITY:
                    self.send_error(403)
                    return
                headers = {name: value for name, value in self.headers.items()
                           if name.lower() not in {'host', 'authorization', 'x-team-user',
                               'x-team-gateway', 'x-client-ip', 'x-forwarded-for', 'forwarded',
                               'connection', 'transfer-encoding'}}
                headers.update({'Host': AUTHORITY, 'X-Team-Gateway': SECRET,
                                'X-Client-IP': self.client_address[0]})
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                connection = http.client.HTTPConnection('127.0.0.1', backend.effective_port, timeout=5)
                try:
                    connection.request(self.command, self.path, body=body, headers=headers)
                    response = connection.getresponse()
                    data = response.read()
                    self.send_response(response.status)
                    for name, value in response.getheaders():
                        if name.lower() not in {'connection', 'transfer-encoding', 'content-length'}:
                            self.send_header(name, value)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                finally:
                    connection.close()

            do_GET = do_POST = forward

        proxy = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
        proxy_worker = threading.Thread(target=proxy.serve_forever, daemon=True)
        proxy_worker.start()

        def call(path, *, direct=False, headers=None, body=None):
            connection = http.client.HTTPConnection(
                '127.0.0.1', backend.effective_port if direct else proxy.server_port, timeout=5)
            try:
                connection.request('POST' if body is not None else 'GET', path,
                                   body=json.dumps(body) if body is not None else None,
                                   headers={'Host': AUTHORITY, **(headers or {})})
                response = connection.getresponse()
                return response.status, response.getheaders(), response.read()
            finally:
                connection.close()

        try:
            assert call('/')[0] == 200  # No separate site-account authentication.
            status, headers, data = call('/api/status', headers={
                'X-Team-User': 'forged-admin', 'X-Team-Gateway': 'forged-secret',
                'X-Client-IP': '198.51.100.1', 'X-Forwarded-For': '198.51.100.1',
                'Authorization': 'Basic synthetic-forged-value'})
            state = json.loads(data)
            assert status == 200 and state['hosted'] is True and state['account'] is None
            assert forwarded == [('127.0.0.1', None, None)] * 2, forwarded
            cookies = SimpleCookie()
            for name, value in headers:
                if name.lower() == 'set-cookie':
                    cookies.load(value)
            assert cookies and all(c['secure'] and c['httponly'] and c['samesite'] == 'Strict'
                                   for c in cookies.values())
            response_headers = dict(headers)
            csp = response_headers['Content-Security-Policy']
            assert "script-src 'self';" in csp and 'unsafe-eval' not in csp
            assert response_headers['Cache-Control'] == 'no-store'
            write_headers = {'Cookie': '; '.join(c.OutputString().split(';')[0] for c in cookies.values()),
                             'Content-Type': 'application/json', 'Origin': ORIGIN,
                             'X-CSRF-Token': state['csrf']}
            # The full external authority, including the HTTPS port, must survive forwarding.
            assert call('/api/location', headers=write_headers, body=POINT)[0] == 200
            for changes in ({'Origin': 'https://team.example.com'}, {'X-CSRF-Token': ''}):
                assert call('/api/location', headers={**write_headers, **changes}, body=POINT)[0] == 403
            assert call('/api/status', headers={'Host': 'team.example.com'})[0] == 403
            assert call('/api/status', direct=True)[0] == 403
            assert call('/healthz', direct=True)[0] == 200
            for host in ('team.example.com', 'other.example.com:60000'):
                assert call('/api/status', direct=True, headers={
                    'Host': host, 'X-Team-Gateway': SECRET, 'X-Client-IP': '127.0.0.1'})[0] == 403
            print('Proxy contract PASS: nonstandard HTTPS origin/Host, page/status=200, '
                  'forged headers overwritten/removed, secure cookies/CSP, Origin/CSRF, '
                  'direct backend=403, health=200; school requests=0')
        finally:
            proxy.shutdown()
            proxy.server_close()
            proxy_worker.join(timeout=3)
            backend.task_dispatcher.shutdown(timeout=5)
            backend.close()
            worker.join(timeout=3)
            app.extensions['workspaces'].close()


if __name__ == '__main__':
    main()
