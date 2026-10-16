import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from termux_history_worker import (
    CATCHUP_INTERVAL_SECONDS,
    INTERVAL_SECONDS,
    next_cycle_delay,
    publish_worker_status,
    run_post_publish_validation,
)


class TermuxHistoryWorkerTests(unittest.TestCase):
    def test_old_worker_cannot_overwrite_newer_status_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'status.json'
            path.write_text(json.dumps({'pid': 999999, 'state': 'COLLECTING'}), encoding='utf-8')
            self.assertFalse(
                publish_worker_status(path, {'state': 'STOPPED'}, owner_only=True)
            )
            self.assertEqual(json.loads(path.read_text())['state'], 'COLLECTING')

    def test_fast_catchup_used_only_for_healthy_incomplete_history(self):
        delay, remaining, fast = next_cycle_delay(
            {'state': 'BOUNDED_PROGRESS', 'daily': {'remaining_archive_days': 900, 'blocked': False}},
            0,
        )
        self.assertEqual(delay, CATCHUP_INTERVAL_SECONDS)
        self.assertEqual(remaining, 900)
        self.assertTrue(fast)

        delay, remaining, fast = next_cycle_delay(
            {'state': 'BLOCKED_BY_NSE_ACCESS', 'daily': {'remaining_archive_days': 900, 'blocked': True}},
            0,
        )
        self.assertEqual(delay, INTERVAL_SECONDS)
        self.assertEqual(remaining, 900)
        self.assertFalse(fast)

        delay, remaining, fast = next_cycle_delay(
            {'state': 'ADJUSTED_HISTORY_PUBLISHED', 'daily': {'remaining_archive_days': 0, 'blocked': False}},
            0,
        )
        self.assertEqual(delay, INTERVAL_SECONDS)
        self.assertEqual(remaining, 0)
        self.assertFalse(fast)

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

            self.assertEqual(first['state'], 'BACKTEST_SCREEN_PAPER_READINESS_COMPLETE')
            self.assertEqual(second['state'], 'ALREADY_VALIDATED_FOR_CURRENT_HISTORY')
            self.assertEqual(run.call_count, 8)
            marker = json.loads((root/'runtime'/'history_backtest_done.json').read_text())
            self.assertEqual(marker['sha256'], 'abc123')
            self.assertEqual(marker['annual_quality_returncode'], 0)
            self.assertEqual(marker['final_shortlist_returncode'], 0)
            self.assertEqual(marker['fast_fundamental_returncode'], 0)
            self.assertEqual(marker['fast_research_returncode'], 0)
            self.assertEqual(marker['backtest_returncode'], 0)
            self.assertEqual(marker['technical_screen_returncode'], 0)
            self.assertEqual(marker['paper_returncode'], 0)
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
            self.assertEqual(run.call_count, 16)

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
