"""Caddy public-entry smoke test; loopback traffic only, no school requests."""
import json
import os
from pathlib import Path
import socket
import sys
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from flask import request
from hosted import HostedSettings
from waitress import create_server

secret = 'synthetic-gateway-secret-' * 3
with tempfile.TemporaryDirectory() as directory:
    app = create_app(directory, hosted=HostedSettings('https://team.example.com', secret))
    forwarded = []

    @app.before_request
    def inspect_proxy_headers():
        forwarded.append((request.headers.get('X-Client-IP'), request.headers.get('X-Team-User'),
                          request.headers.get('Authorization')))

    backend = create_server(app, host='127.0.0.1', port=0, threads=4)
    threading.Thread(target=backend.run, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    path = Path(directory)
    config = (ROOT / 'deploy' / 'Caddyfile').read_text()
    config = config.replace('{$SITE_DOMAIN} {', 'http://team.example.com:'+str(port)+' {\n\tbind 127.0.0.1')
    config = config.replace('backend:8090', '127.0.0.1:'+str(backend.effective_port))
    config = config.replace('header_up -Authorization', 'header_up -Authorization\n\t\theader_up Host team.example.com')
    (path/'Caddyfile').write_text(config)
    env = {**os.environ, 'SITE_DOMAIN':'team.example.com', 'ACME_EMAIL':'admin@example.com', 'GATEWAY_SECRET':secret,
           'XDG_DATA_HOME':str(path/'data'), 'XDG_CONFIG_HOME':str(path/'config')}
    process = subprocess.Popen([os.environ.get('CADDY_BIN', 'caddy'),'run','--config',str(path/'Caddyfile'),'--adapter','caddyfile'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = 'http://127.0.0.1:'+str(port)+'/api/status'
    try:
        for _ in range(50):
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError('Caddy did not start')
        headers={'Host':'team.example.com:'+str(port)}
        # The page and status must be reachable without any extra site password.
        with opener.open(urllib.request.Request(url.removesuffix('api/status'), headers=headers)) as response:
            assert response.status == 200
        headers.update({'X-Team-User': 'forged-admin', 'X-Team-Gateway': 'forged-secret',
                        'X-Client-IP': '198.51.100.1', 'X-Forwarded-For': '198.51.100.1',
                        'Authorization': 'Basic synthetic-forged-value'})
        with opener.open(urllib.request.Request(url,headers=headers)) as response:
            assert response.status == 200
            state = json.load(response)
            assert state['hosted'] is True and state['account'] is None
        assert len(forwarded) == 2 and all(item == ('127.0.0.1', None, None) for item in forwarded), forwarded
        try:
            opener.open(urllib.request.Request('http://127.0.0.1:'+str(backend.effective_port)+'/api/status',
                                               headers={'Host': 'team.example.com'}))
        except urllib.error.HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError('Direct backend request bypassed trusted proxy requirement')
        print('Caddy gateway PASS: public page/status=200, forged headers overwritten/removed, '
              'direct backend=403; school requests=0')
    finally:
        process.terminate()
        process.wait(timeout=10)
        backend.task_dispatcher.shutdown()
        backend.close()
        app.extensions['workspaces'].close()
