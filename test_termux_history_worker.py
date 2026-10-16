import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from termux_history_worker import run_post_publish_validation


class TermuxHistoryWorkerTests(unittest.TestCase):
    def test_post_publish_backtest_runs_once_per_history_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'runtime').mkdir()
            (root/'market_data_metadata.json').write_text(
                json.dumps({'sha256': 'abc123'}),
                encoding='utf-8',
            )
            completed = Mock(returncode=0)
            with patch('termux_history_worker.subprocess.run', return_value=completed) as run:
                first = run_post_publish_validation(root, {}, io.StringIO())
                second = run_post_publish_validation(root, {}, io.StringIO())

            self.assertEqual(first['state'], 'BACKTEST_AND_READINESS_COMPLETE')
            self.assertEqual(second['state'], 'ALREADY_VALIDATED_FOR_CURRENT_HISTORY')
            self.assertEqual(run.call_count, 2)
            marker = json.loads((root/'runtime'/'history_backtest_done.json').read_text())
            self.assertEqual(marker['sha256'], 'abc123')
            self.assertEqual(marker['backtest_returncode'], 0)
            self.assertEqual(marker['readiness_returncode'], 0)

    def test_new_history_sha_triggers_new_backtest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'runtime').mkdir()
            meta = root/'market_data_metadata.json'
            meta.write_text(json.dumps({'sha256': 'first'}), encoding='utf-8')
            completed = Mock(returncode=0)
            with patch('termux_history_worker.subprocess.run', return_value=completed) as run:
                run_post_publish_validation(root, {}, io.StringIO())
                meta.write_text(json.dumps({'sha256': 'second'}), encoding='utf-8')
                result = run_post_publish_validation(root, {}, io.StringIO())
            self.assertEqual(result['sha256'], 'second')
            self.assertEqual(run.call_count, 4)

    def test_missing_metadata_keeps_validation_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'runtime').mkdir()
            with patch('termux_history_worker.subprocess.run') as run:
                result = run_post_publish_validation(root, {}, io.StringIO())
            self.assertEqual(result['state'], 'WAITING_FOR_PUBLISHED_HISTORY_METADATA')
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
