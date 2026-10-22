"""Public map settings, provider selection and restricted image origins."""
import json
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from app import create_app
from hosted import HostedSettings
from web_support import BASE, GATEWAY

KEY = 'synthetic-tianditu-browser-key'


class MapSettingsTests(unittest.TestCase):
    def test_existing_deployments_without_a_key_keep_osm(self):
        settings = HostedSettings(BASE, GATEWAY['X-Team-Gateway'])
        config = settings.map_config()
        self.assertEqual(config['default'], 'osm')
        self.assertEqual([p['id'] for p in config['providers']], ['osm'])
        self.assertNotIn('tianditu', settings.map_image_sources())

    def test_auto_prefers_tianditu_and_builds_both_official_wmts_layers(self):
        settings = HostedSettings(BASE, GATEWAY['X-Team-Gateway'], tianditu_key=KEY)
        config = settings.map_config()
        self.assertEqual(config['default'], 'tianditu')
        self.assertEqual([p['id'] for p in config['providers']], ['tianditu', 'osm'])
        provider = config['providers'][0]
        self.assertEqual(provider['reference'], 'CGCS2000')
        for spec, name in zip(provider['layers'], ('vec', 'cva'), strict=True):
            parsed = urlsplit(spec['url'])
            self.assertEqual(parsed.scheme, 'https')
            self.assertEqual(parsed.netloc, 't{s}.tianditu.gov.cn')
            self.assertEqual(parsed.path, '/' + name + '_w/wmts')
            params = parse_qs(parsed.query)
            for key, value in {'SERVICE': 'WMTS', 'REQUEST': 'GetTile', 'LAYER': name,
                               'TILEMATRIXSET': 'w', 'TILEMATRIX': '{z}',
                               'TILEROW': '{y}', 'TILECOL': '{x}', 'tk': KEY}.items():
                self.assertEqual(params[key], [value])
            self.assertEqual(spec['subdomains'], '01234567')
        self.assertIn('天地图', provider['layers'][0]['attribution'])
        self.assertNotIn(GATEWAY['X-Team-Gateway'], json.dumps(config))

    def test_explicit_provider_and_invalid_map_settings(self):
        config = HostedSettings(BASE, GATEWAY['X-Team-Gateway'],
                                map_provider='osm', tianditu_key=KEY).map_config()
        self.assertEqual(config['default'], 'osm')
        for kwargs in ({'map_provider': 'unknown'}, {'map_provider': 'tianditu'},
                       {'tianditu_key': 'unsafe&key=other'}, {'tianditu_key': 'unsafe\nkey'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                HostedSettings(BASE, GATEWAY['X-Team-Gateway'], **kwargs)

    def test_map_environment_is_loaded_without_changing_workspace_limits(self):
        with patch.dict('os.environ', {'PUBLIC_ORIGIN': BASE, 'GATEWAY_SECRET': 'a'*32,
                                     'MAP_PROVIDER': ' tianditu ', 'TIANDITU_KEY': KEY,
                                     'MAX_CONCURRENT_LOGINS': '1'}, clear=True):
            settings = HostedSettings.from_env()
        self.assertEqual(settings.map_config()['default'], 'tianditu')
        self.assertEqual(settings.tianditu_key, KEY)
        self.assertEqual(settings.max_logins, 1)

    def test_config_needs_trusted_proxy_but_not_school_login_and_csp_stays_restricted(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, hosted=HostedSettings(
                BASE, GATEWAY['X-Team-Gateway'], map_provider='tianditu', tianditu_key=KEY))
            self.addCleanup(app.extensions['workspaces'].close)
            client = app.test_client()
            self.assertEqual(client.get('/api/map-config', base_url=BASE).status_code, 403)
            response = client.get('/api/map-config', base_url=BASE, headers=GATEWAY)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['default'], 'tianditu')
            self.assertNotIn(GATEWAY['X-Team-Gateway'], response.get_data(as_text=True))
            self.assertNotIn('cookies', response.json)
            csp = response.headers['Content-Security-Policy']
            for i in range(8):
                self.assertIn(f'https://t{i}.tianditu.gov.cn', csp)
            self.assertIn("script-src 'self';", csp)
            self.assertIn("connect-src 'self';", csp)
            self.assertNotIn('*', csp)
            self.assertNotIn('unsafe-eval', csp)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')


if __name__ == '__main__':
    unittest.main()
