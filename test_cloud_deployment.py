"""Persistence, startup isolation and health checks without real credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from app import create_app
from cloud_recovery_codes import auth_db_path
from cloud_start import SOURCE_FILES, configure, seed_sources, stop_children
from research_monitor.paths import CODE_ROOT, data_root
from test_app_security import HASH, SECRET


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_data_root_requires_absolute_path(self):
        with patch.dict(os.environ, {'NSE_DATA_DIR': 'relative'}):
            with self.assertRaises(ValueError):
                data_root()
        with patch.dict(os.environ, {'NSE_DATA_DIR': str(self.root)}):
            self.assertEqual(data_root(), self.root.resolve())

    def test_all_data_modules_use_persistent_root_in_new_process(self):
        names = ['app', 'annual_fundamental_quality', 'final_fundamental_shortlist',
                 'nse_quarterly_monitor', 'nse_annual_history', 'swing_worker',
                 'swing_backtest', 'swing_strategy', 'swing_technical_screener',
                 'swing_import_history', 'nse_legacy_xbrl_verify']
        code = 'import importlib,json; print(json.dumps([str(importlib.import_module(n).ROOT) for n in '+repr(names)+']))'
        env = dict(os.environ, NSE_DATA_DIR=str(self.root))
        result = subprocess.run([sys.executable, '-c', code], env=env, cwd=CODE_ROOT,
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [str(self.root)]*len(names))

    def test_redeploy_preserves_private_data_refreshes_public_snapshots(self):
        source, data = self.root/'source', self.root/'data'
        source.mkdir(); data.mkdir()
        for name in SOURCE_FILES:
            (source/name).write_text('public version 1')
        private = ['market_ohlcv.csv', 'market_data_metadata.json', 'swing_watchlist.json']
        for name in private:
            (data/name).write_text('private unchanged')
        (data/'runtime').mkdir()
        for name in ['paper.sqlite', 'auth.sqlite', 'schedule.sqlite']:
            (data/'runtime'/name).write_bytes(b'persisted')
        (data/'swing_candidates_manifest.json').write_text('{}')
        seed_sources(source, data)
        (source/SOURCE_FILES[0]).write_text('public version 2')
        seed_sources(source, data)
        self.assertEqual((data/SOURCE_FILES[0]).read_text(), 'public version 2')
        for name in private:
            self.assertEqual((data/name).read_text(), 'private unchanged')
        for name in ['paper.sqlite', 'auth.sqlite', 'schedule.sqlite']:
            self.assertEqual((data/'runtime'/name).read_bytes(), b'persisted')
        self.assertFalse((data/'swing_candidates_manifest.json').exists())

    def test_missing_snapshot_does_not_partially_seed(self):
        source, data = self.root/'source', self.root/'data'
        source.mkdir(); data.mkdir()
        (source/SOURCE_FILES[0]).write_text('new')
        (data/SOURCE_FILES[0]).write_text('existing')
        with self.assertRaises(RuntimeError):
            seed_sources(source, data)
        self.assertEqual((data/SOURCE_FILES[0]).read_text(), 'existing')

    def test_cloud_settings_require_host_and_persistent_storage(self):
        env = dict(RENDER='true', NSE_DATA_DIR=str(self.root),
                   RENDER_EXTERNAL_HOSTNAME='test.onrender.com', APP_SECRET_KEY='x'*48,
                   APP_PASSWORD_HASH=HASH, APP_TOTP_SECRET=SECRET, SMS_ENABLED='1')
        self.assertEqual(configure(env, self.root), 10000)
        self.assertEqual(env['APP_HOST'], 'test.onrender.com')
        self.assertEqual(env['APP_AUTH_DB'], str(self.root/'runtime'/'auth.sqlite'))
        self.assertEqual(env['SMS_ENABLED'], '0')
        with self.assertRaises(RuntimeError):
            configure(env, CODE_ROOT)
        env.pop('RENDER')
        with self.assertRaises(RuntimeError):
            configure(env, self.root)

    def test_health_probe_does_not_weaken_private_routes(self):
        cfg = dict(APP_SECRET_KEY='x'*48, APP_PASSWORD_HASH=HASH, APP_TOTP_SECRET=SECRET,
                   APP_AUTH_DB=str(self.root/'auth.sqlite'), APP_HOST='localhost', CLOUD_SUPERVISED='1')
        app = create_app(cfg)
        client = app.test_client()
        with patch('app.ROOT', self.root):
            self.assertEqual(client.get('/healthz').status_code, 503)
            (self.root/'runtime').mkdir()
            path = self.root/'runtime'/'cloud_health.json'
            path.write_text(json.dumps(dict(updated_at=time.time(), processes_alive=True)))
            response = client.get('/healthz')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {'status': 'ok'})
            self.assertNotIn('Set-Cookie', response.headers)
            self.assertEqual(client.get('/login').status_code, 400)
            self.assertEqual(client.get('/', base_url='https://localhost').status_code, 302)
            self.assertEqual(client.post('/healthz').status_code, 400)
            self.assertEqual(client.get('/healthz', base_url='http://wrong.test').status_code, 400)
            path.write_text(json.dumps(dict(updated_at=time.time()-30, processes_alive=True)))
            self.assertEqual(client.get('/healthz').status_code, 503)

    def test_recovery_code_rotation_uses_enrolled_absolute_database(self):
        enrolled = self.root/'runtime'/'auth.sqlite'
        self.assertEqual(auth_db_path({'APP_AUTH_DB': str(enrolled)}), enrolled.resolve())
        with self.assertRaises(ValueError):
            auth_db_path({})
        with self.assertRaises(ValueError):
            auth_db_path({'APP_AUTH_DB': 'relative.sqlite'})

    def test_stop_terminates_child_processes(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'], start_new_session=True)
        stop_children([child])
        self.assertIsNotNone(child.poll())

    def test_no_calendar_still_runs_offline_checks_using_code_directory(self):
        from swing_worker import tick
        from datetime import datetime, timezone
        with patch('swing_worker.subprocess.run') as run:
            run.return_value.returncode = 0
            with patch.dict(os.environ, {'UPSTOX_ANALYTICS_TOKEN': ''}):
                state = tick(self.root, datetime(2026, 10, 16, 4, tzinfo=timezone.utc))
        self.assertEqual(state['phase'], 'CALENDAR_PENDING')
        self.assertEqual(state['live_feed'], 'NOT_CONNECTED')
        self.assertIn('BLOCKED', state['entry_alerts'])
        self.assertEqual(len(run.call_args_list), 6)
        for call in run.call_args_list:
            self.assertEqual(Path(call.args[0][1]).parent, CODE_ROOT)
        self.assertTrue((self.root/'runtime'/'schedule.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
