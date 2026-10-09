import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import socketserver
from http.server import BaseHTTPRequestHandler
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from wda_security import auth_token, read_token, security_digest
from wda_setup import SetupManager, _fingerprint, WDA_COMMIT, _write_json
from wda_client import WDAClient, WDAError


class SecurityTests(unittest.TestCase):
    def test_private_transport_authenticates_every_request_and_reconnect(self):
        with tempfile.TemporaryDirectory(prefix='fiu-', dir='/tmp') as directory:
            root = Path(directory)
            token = auth_token(root)
            requests = []

            class Handler(BaseHTTPRequestHandler):
                protocol_version = 'HTTP/1.1'

                def log_message(self, *args):
                    pass

                def do_GET(self):
                    authorized = self.headers.get('Authorization') == 'Bearer ' + token
                    requests.append(authorized)
                    body = json.dumps({'value': {'ready': authorized}}).encode()
                    self.send_response(200 if authorized else 401)
                    self.send_header('X-iPhone-Use-Security', 'usb-auth-v1')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            server = socketserver.UnixStreamServer(str(root / 'control.sock'), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = WDAClient(auth_token=token, socket_path=root / 'control.sock')
                for i in range(3):
                    self.assertTrue(client.request('GET', '/status')['value']['ready'])
                    client.close()
                wrong = WDAClient(auth_token='b' * 64, socket_path=root / 'control.sock')
                with self.assertRaises(WDAError):
                    wrong.request('GET', '/status')
                wrong.close()
                self.assertEqual(requests, [True, True, True, False])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_xctestrun_secret_is_private_and_not_a_command_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = SetupManager(Path(directory) / 'state')
            products = manager.state_dir / 'derived_data/Build/Products'
            products.mkdir(parents=True)
            import plistlib
            source = products / 'Runner.xctestrun'
            source.write_bytes(plistlib.dumps({'TestConfigurations': [{'TestTargets': [
                {'TestBundlePath': 'fixture', 'EnvironmentVariables': {'USE_IP': ''}}]}]}))
            result = manager._prepare_test_run()
            target = plistlib.loads(result.read_bytes())['TestConfigurations'][0]['TestTargets'][0]
            self.assertEqual(target['EnvironmentVariables']['IPHONE_USE_AUTH_TOKEN'], auth_token(manager.state_dir))
            self.assertEqual(target['EnvironmentVariables']['USE_IP'], '127.0.0.1')
            self.assertEqual(result.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(auth_token(manager.state_dir), ' '.join(manager._build_command({'udid':'TEST-DEVICE'}, 'test-without-building')))

    def test_token_is_private_persistent_and_never_repaired_unsafely(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = auth_token(root)
            self.assertRegex(token, r'^[a-f0-9]{64}$')
            self.assertEqual(auth_token(root), token)
            self.assertEqual(read_token(root / 'auth-token'), token)
            self.assertEqual((root / 'auth-token').stat().st_mode & 0o777, 0o600)
            (root / 'auth-token').chmod(0o644)
            with self.assertRaises(ValueError):
                auth_token(root)

    def test_symlink_and_malformed_credentials_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = root / 'actual'
            actual.write_text('a' * 64)
            actual.chmod(0o600)
            (root / 'auth-token').symlink_to(actual)
            with self.assertRaises((ValueError, OSError)):
                auth_token(root)
            (root / 'auth-token').unlink()
            (root / 'auth-token').write_text('not-a-token')
            (root / 'auth-token').chmod(0o600)
            with self.assertRaises(ValueError):
                auth_token(root)

    def test_legacy_build_marker_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = SetupManager(Path(directory) / 'state')
            config = {'example': True}
            _write_json(manager.state_dir / 'build.json', {
                'config_fingerprint': _fingerprint(config), 'commit': WDA_COMMIT})
            with self.assertRaisesRegex(ValueError, 'build'):
                manager._require_build(config)

    def test_build_binds_http_and_tracks_patch_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = SetupManager(Path(directory) / 'state')
            manager.setup('configure', udid='TEST-DEVICE-1234', team_id='ABCDE12345',
                          bundle_id='com.example.wda')
            command = manager._build_command(manager.config, 'build-for-testing')
            self.assertIn('USE_IP=127.0.0.1', command)
            self.assertNotIn(auth_token(manager.state_dir), ' '.join(command))
            self.assertIn(security_digest(), ' '.join(command))


if __name__ == '__main__':
    unittest.main()
