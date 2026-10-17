import io
import json
import tempfile
import unittest
from pathlib import Path
from datetime import date, datetime, timezone
from unittest.mock import Mock, patch

from termux_history_worker import (
    CATCHUP_INTERVAL_SECONDS,
    INTERVAL_SECONDS,
    next_cycle_delay,
    legacy_history_recheck_needed,
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

    def test_new_mainboard_history_drives_catchup_even_when_adjusted_complete(self):
        completed = {
            'state':'ADJUSTED_HISTORY_PUBLISHED',
            'daily':{'remaining_archive_days':0,'blocked':False}
        }
        raw = {'state':'RESEARCH_UNADJUSTED_EOD_PROGRESS',
               'estimated_outstanding_expected_sessions':1681,
               'source_blocked':False}
        delay, old_pending, fast = next_cycle_delay(completed,0,raw_backfill=raw)
        self.assertEqual(delay,CATCHUP_INTERVAL_SECONDS)
        self.assertEqual(old_pending,0)
        self.assertTrue(fast)
        raw['state'] = 'NSE_ACCESS_BLOCKED'
        raw['source_blocked'] = True
        self.assertEqual(next_cycle_delay(completed,0,raw_backfill=raw)[0],
                         INTERVAL_SECONDS)

    def test_adjusted_complete_skips_only_until_next_completed_reviewed_session(self):
        completed = {
            'state':'ADJUSTED_HISTORY_PUBLISHED',
            'publish':{'published':True},
            'daily_range_complete':True,
            'corporate_action_range_complete':True,
            'suspicious_consecutive_no_file_gap':False,
            'range_end':'2026-10-16'
        }
        # Sat 17 Oct 2026 in India, previous completed session was Friday.
        saturday=datetime(2026,10,17,8,tzinfo=timezone.utc)
        self.assertFalse(legacy_history_recheck_needed(completed,saturday))
        # Monday before market opens: no new completed session.
        mon_early=datetime(2026,10,19,2,tzinfo=timezone.utc)
        self.assertFalse(legacy_history_recheck_needed(completed,mon_early))
        # Tue Oct 20 is a reviewed NSE holiday: Monday becomes due.
        tue=datetime(2026,10,20,2,tzinfo=timezone.utc)
        self.assertTrue(legacy_history_recheck_needed(completed,tue))
        completed['range_end']='2026-10-19'
        self.assertFalse(legacy_history_recheck_needed(completed,tue))
        completed['publish']={'published':False}
        self.assertTrue(legacy_history_recheck_needed(completed,tue))

    def test_future_or_unattested_adjusted_history_does_not_skip(self):
        now=datetime(2026,10,17,8,tzinfo=timezone.utc)
        partial={'state':'ADJUSTED_HISTORY_PUBLISHED','publish':{'published':True},
                 'range_end':'2026-10-16','daily_range_complete':False,
                 'corporate_action_range_complete':True}
        self.assertTrue(legacy_history_recheck_needed(partial,now))
        partial['daily_range_complete']=True
        partial['range_end']='2026-12-31'
        self.assertTrue(legacy_history_recheck_needed(partial,now))
        with self.assertRaisesRegex(ValueError,'TIMEZONE'):
            legacy_history_recheck_needed(partial,datetime(2026,10,17,8))

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
