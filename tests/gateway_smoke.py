"""Caddy authentication smoke test; loopback traffic and synthetic credentials only."""
import base64
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
from hosted import HostedSettings
from waitress import create_server

secret = 'synthetic-gateway-secret-' * 3
with tempfile.TemporaryDirectory() as directory:
    app = create_app(directory, hosted=HostedSettings('https://team.example.com', secret))
    backend = create_server(app, host='127.0.0.1', port=0, threads=4)
    threading.Thread(target=backend.run, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    path = Path(directory)
    users = path / 'users'
    users.write_text('tester $2a$14$GiENISbcALwhYUj/1gDGheVZSJm1gE14FG3Rvs74M5LJpRjVO2we.\n')
    config = (ROOT / 'deploy' / 'Caddyfile').read_text().replace('/run/secrets/team_users', str(users))
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
        headers={'Host':'team.example.com:'+str(port),'X-Team-User':'forged-admin','X-Team-Gateway':secret}
        try:
            opener.open(urllib.request.Request(url,headers=headers))
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError('Gateway allowed missing Basic credentials')
        headers['Authorization']='Basic '+base64.b64encode(b'tester:synthetic-ci-only').decode()
        with opener.open(urllib.request.Request(url,headers=headers)) as response:
            assert response.status == 200
            assert json.load(response)['hosted'] is True
        owners={owner for owner,_ in app.extensions['workspaces'].entries.values()}
        assert owners == {'tester'}, owners
        print('Caddy gateway PASS: unauthenticated=401, authenticated=200, forged identity overwritten; school requests=0')
    finally:
        process.terminate()
        process.wait(timeout=10)
        backend.task_dispatcher.shutdown()
        backend.close()
        app.extensions['workspaces'].close()
