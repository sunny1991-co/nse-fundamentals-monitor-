"""Synthetic data only: validates failure gates, not strategy profitability."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from research_monitor.history import validate_history, coverage
from research_monitor.readiness import candidate_manifest, candidate_rows, snapshot, EVIDENCE_FILES
from swing_import_history import import_history


def fixture(body='TEST,2020-01-01,100,110,90,105,1000\n'):
    payload = ('Symbol,Date,Open,High,Low,Close,Volume\n'+body).encode()
    return payload, dict(sha256=hashlib.sha256(payload).hexdigest(),
        source='SYNTHETIC TEST ONLY', adjustment_method='Synthetic fixture, no real corporate actions',
        corporate_actions_adjusted=True)


class HistoryTests(unittest.TestCase):
    def test_valid_history_does_not_imply_adequate_backtest(self):
        frame, _ = validate_history(*fixture(), today='2026-10-16')
        self.assertFalse(coverage(frame)[0]['Minimum_History_Met'])

    def test_digest_and_attestation_are_required(self):
        data, meta = fixture()
        for change in ({'sha256':'bad'}, {'corporate_actions_adjusted':'true'},
                       {'source':' '}, {'adjustment_method':False}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_history(data, dict(meta, **change))

    def test_invalid_bars(self):
        for body in (' ,2020-01-01,100,110,90,105,1000\n',
                     'TEST,2020-01-01T09:15:00,100,110,90,105,1000\n',
                     'TEST,2020-02-30,100,110,90,105,1000\n',
                     'TEST,2099-01-01,100,110,90,105,1000\n',
                     'TEST,2020-01-01,100,99,90,105,1000\n',
                     'TEST,2020-01-01,100,110,90,105,-1\n',
                     'TEST,2020-01-01,NaN,110,90,105,1000\n',
                     'TEST,2020-01-01,100,110,90,105,inf\n',
                     'TEST,2020-01-01,100,110,90,105,1000\n'*2):
            with self.subTest(body=body), self.assertRaises(ValueError):
                validate_history(*fixture(body), today='2026-10-16')

    def test_today_excluded_and_no_completed_bars_rejected(self):
        body='TEST,2026-10-16,100,110,90,105,1000\n'
        with self.assertRaises(ValueError):
            validate_history(*fixture(body), today='2026-10-16')
        frame, _ = validate_history(*fixture('TEST,2026-10-15,100,110,90,105,1000\n'+body), today='2026-10-16')
        self.assertEqual(len(frame), 1)

    def test_import_invalid_input_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); src=root/'source.csv'; meta=root/'source.json'
            (root/'market_ohlcv.csv').write_text('existing')
            (root/'swing_trade_candidates.csv').write_text('existing plans')
            data, metadata=fixture();src.write_bytes(data);metadata['sha256']='invalid'
            meta.write_text(json.dumps(metadata))
            with self.assertRaises(ValueError): import_history(src,meta,root)
            self.assertEqual((root/'market_ohlcv.csv').read_text(),'existing')
            self.assertEqual((root/'swing_trade_candidates.csv').read_text(),'existing plans')

    def test_import_preserves_exact_bytes_and_invalidates_old_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); src=root/'source.csv'; meta=root/'source.json'
            data, metadata=fixture();src.write_bytes(data);meta.write_text(json.dumps(metadata))
            for name in ('swing_backtest_report.csv','swing_trade_candidates.csv','swing_candidates_manifest.json'):
                (root/name).write_text('old')
            report=import_history(src,meta,root)
            self.assertEqual((root/'market_ohlcv.csv').read_bytes(),data)
            self.assertEqual(report['data_sha256'],metadata['sha256'])
            self.assertFalse((root/'swing_trade_candidates.csv').exists())


class PlanReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=datetime.now(timezone.utc)

    def plans(self):
        for name in EVIDENCE_FILES: (self.root/name).write_text('synthetic input')
        (self.root/'swing_trade_candidates.csv').write_text('Symbol,Status\nTEST,Conditional\n')
        (self.root/'swing_candidates_manifest.json').write_text(json.dumps(candidate_manifest(self.root,self.now)))

    def test_only_fresh_matching_plans_display(self):
        self.plans()
        self.assertEqual(candidate_rows(self.root,self.now)[0][0]['Symbol'],'TEST')
        self.assertEqual(candidate_rows(self.root,self.now+timedelta(days=2))[0],[])
        self.assertEqual(candidate_rows(self.root,self.now-timedelta(seconds=1))[0],[])

    def test_all_changed_evidence_files_hide_plans(self):
        for name in EVIDENCE_FILES:
            with self.subTest(name=name):
                self.plans();(self.root/name).write_text('changed')
                self.assertEqual(candidate_rows(self.root,self.now)[0],[])

    def test_legacy_plans_without_manifest_hidden(self):
        self.plans();(self.root/'swing_candidates_manifest.json').unlink()
        self.assertEqual(candidate_rows(self.root,self.now)[0],[])

    def test_readiness_no_account_no_network_no_false_pass(self):
        with patch('requests.get') as get, patch('requests.post') as post:
            result=snapshot(self.root,self.now)
            get.assert_not_called();post.assert_not_called()
        self.assertEqual(result['historical_data'],'MISSING')
        self.assertEqual(result['entry_alerts'],'BLOCKED')
        self.assertEqual(result['live_feed'],'NOT_VERIFIED')

    def test_stale_worker_never_implies_live_connection(self):
        (self.root/'runtime').mkdir()
        (self.root/'runtime'/'worker_status.json').write_text(json.dumps(dict(
            updated_at=(self.now-timedelta(minutes=5)).isoformat(),live_feed='POLLING_10_SECONDS')))
        self.assertEqual(snapshot(self.root,self.now)['live_feed'],'WORKER_STATUS_STALE')

    def test_failed_screener_removes_prior_manifest_and_plans(self):
        import swing_technical_screener as screener
        self.plans()
        with patch.object(screener,'ROOT',self.root), patch.object(screener,'OUTPUT',self.root/'swing_trade_candidates.csv'), patch.object(screener,'run_backtests',side_effect=ValueError('synthetic failure')):
            with self.assertRaises(ValueError): screener.main()
        self.assertFalse((self.root/'swing_candidates_manifest.json').exists())
        self.assertFalse((self.root/'swing_trade_candidates.csv').exists())

    def test_missing_history_is_waiting_state_not_exception(self):
        import swing_technical_screener as screener
        self.plans()
        with patch.object(screener,'ROOT',self.root), patch.object(screener,'OUTPUT',self.root/'swing_trade_candidates.csv'), patch.object(screener,'run_backtests') as run_backtests:
            for name in ('market_ohlcv.csv','market_data_metadata.json'):
                (self.root/name).unlink(missing_ok=True)
            screener.main()
            run_backtests.assert_not_called()
        self.assertFalse((self.root/'swing_candidates_manifest.json').exists())
        self.assertFalse((self.root/'swing_trade_candidates.csv').exists())

    def test_offline_pipeline_with_no_eligible_stock_produces_no_plan(self):
        import pandas as pd
        import swing_backtest as backtest
        import swing_technical_screener as screener
        dates=pd.bdate_range('2010-01-01',periods=1260)
        body=''.join(f'TEST,{d.date()},100,110,90,105,1000\n' for d in dates)
        data,meta=fixture(body)
        (self.root/'market_ohlcv.csv').write_bytes(data)
        (self.root/'market_data_metadata.json').write_text(json.dumps(meta))
        (self.root/'final_fundamental_shortlist.csv').write_text('Symbol,Final_Fundamental_Criteria_Pass\n')
        today=pd.Timestamp.now(tz='Asia/Kolkata').date().isoformat()
        (self.root/'final_fundamental_screen_summary.csv').write_text('Report_Generated_On,Final_Shortlist_Count\n'+today+',0\n')
        with patch.object(backtest,'ROOT',self.root), patch.object(screener,'ROOT',self.root), patch.object(screener,'OUTPUT',self.root/'swing_trade_candidates.csv'):
            screener.main()
        rows,status=candidate_rows(self.root)
        self.assertEqual(rows,[])
        self.assertEqual(status,'CONDITIONAL_RESEARCH_ONLY')
        self.assertEqual(snapshot(self.root)['technical_pass_matching_data'],0)


if __name__ == '__main__':
    unittest.main()
