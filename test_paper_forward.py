"""Synthetic forward-ledger behavior, not a record of real market performance."""
import json
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, Mock
import pandas as pd
from research_monitor.paper import connect, digest_bars, register, reconcile, report, run
from swing_backtest import simulate, VERSION


def clock(day,hour='08:00:00'):
    return datetime.fromisoformat(day+'T'+hour+'+05:30')


def calendar(day,trading=True):
    return dict(verified=True,source='SYNTHETIC_TEST_CALENDAR',valid_from=day,valid_to=day,
        sessions={day:dict(open=clock(day,'09:15:00').isoformat(),close=clock(day,'15:30:00').isoformat())} if trading else {})


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.prefix=pd.DataFrame([dict(Symbol='TEST',Date=day,Open=100.,High=101.,Low=99.,Close=100.,Volume=1e6) for day in pd.date_range(end='2026-10-15',periods=220)])
        self.cal={d:calendar(d,trading) for d,trading in [('2026-10-16',True),('2026-10-17',False),('2026-10-18',False),('2026-10-19',True)]}

    def plan(self,registered=None):
        p=dict(strategy=VERSION,entry=100.,stop=95.,history_rows=220,history_hash=digest_bars(self.prefix))
        with closing(connect(self.root)) as db,db:
            db.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?)',('test','TEST','2026-10-15',(registered or clock('2026-10-16')).isoformat(),json.dumps(p),'WAITING_FOR_COMPLETED_SESSION','{}'))

    def bars(self,*days):
        return pd.concat([self.prefix,pd.DataFrame([dict(Symbol='TEST',Date=pd.Timestamp(day),Open=100.,High=101.,Low=99.,Close=100.,Volume=1e6) for day in days])],ignore_index=True)

    def state(self):
        with closing(connect(self.root)) as db:
            row=db.execute('SELECT status,state FROM plans').fetchone()
            return row[0],json.loads(row[1])

    def test_open_position_not_forced_closed_at_available_data_end(self):
        self.plan();raw=self.bars('2026-10-16')
        reconcile(self.root,raw,self.cal,clock('2026-10-17'))
        status,state=self.state()
        self.assertEqual(status,'OPEN');self.assertEqual(state['trades'],[])
        self.assertIsNotNone(state['position']['position'])
        # Replaying the same completed bar does not create another entry.
        reconcile(self.root,raw,self.cal,clock('2026-10-17'))
        self.assertEqual(self.state(),(status,state))

    def test_after_open_registration_never_backfills_entry(self):
        self.plan(clock('2026-10-16','10:00:00'))
        reconcile(self.root,self.bars('2026-10-16'),self.cal,clock('2026-10-17'))
        self.assertEqual(self.state()[0],'MISSED_ENTRY_REGISTRATION')

    def test_missing_calendar_or_bar_blocks_execution(self):
        self.plan();raw=self.bars('2026-10-16')
        reconcile(self.root,raw,{},clock('2026-10-17'))
        self.assertTrue(self.state()[0].startswith('WAITING'))
        reconcile(self.root,raw,self.cal,clock('2026-10-20'))
        self.assertTrue(self.state()[0].startswith('WAITING'))

    def test_revised_pre_signal_history_requires_review(self):
        self.plan();raw=self.bars('2026-10-16');raw.loc[0,'Close']=100.5
        reconcile(self.root,raw,self.cal,clock('2026-10-17'))
        self.assertEqual(self.state()[0],'SOURCE_REVISION_REVIEW_REQUIRED')

    def test_revised_observed_bar_requires_review(self):
        self.plan();raw=self.bars('2026-10-16')
        reconcile(self.root,raw,self.cal,clock('2026-10-17'))
        raw.loc[220,'High']=100.5
        reconcile(self.root,raw,self.cal,clock('2026-10-17'))
        self.assertEqual(self.state()[0],'SOURCE_REVISION_REVIEW_REQUIRED')

    def test_gap_stop_is_recorded_once_at_gapped_price(self):
        self.plan();raw=self.bars('2026-10-16','2026-10-19')
        raw.loc[221,['Open','High','Low','Close']]=[80.,82.,79.,81.]
        reconcile(self.root,raw,self.cal,clock('2026-10-20'))
        status,state=self.state()
        self.assertEqual(status,'CLOSED')
        self.assertEqual(len(state['trades']),1)
        self.assertAlmostEqual(state['trades'][0]['Exit'],79.92)
        reconcile(self.root,raw,self.cal,clock('2026-10-20'))
        self.assertEqual(self.state(),(status,state))
        self.assertFalse(report(self.root,clock('2026-10-20'))['forward_validation_pass'])

    def test_entry_outside_range_skipped_not_chased(self):
        self.plan();raw=self.bars('2026-10-16');raw.loc[220,['Open','High','Low','Close']]=[103.,104.,102.,103.]
        reconcile(self.root,raw,self.cal,clock('2026-10-17'))
        self.assertEqual(self.state()[0],'SKIPPED_ENTRY')

    def test_registration_deduplicates_and_freezes_timestamp(self):
        raw=pd.DataFrame([dict(Symbol='TEST',Date=day,Open=100.,High=101.,Low=99.,Close=100.,Volume=1e6) for day in pd.date_range(end='2026-10-15',periods=1260)])
        computed=raw.copy();computed['Signal']=True;computed['Stop']=95.
        candidate=dict(Symbol='TEST',Signal_Date='2026-10-15',Strategy=VERSION)
        with patch('research_monitor.paper.candidate_rows',return_value=([candidate],'CONDITIONAL_RESEARCH_ONLY')),patch('research_monitor.paper.features',return_value=computed):
            self.assertEqual(register(self.root,raw,clock('2026-10-16'))[0],1)
            self.assertEqual(register(self.root,raw,clock('2026-10-16','10:00:00'))[0],0)
        with closing(connect(self.root)) as db:
            self.assertEqual(db.execute('SELECT registered_at FROM plans').fetchone()[0],clock('2026-10-16').isoformat())

    def test_missing_history_creates_no_fake_trades_or_network_calls(self):
        with patch('requests.get') as get,patch('requests.post') as post:
            result=run(self.root,clock('2026-10-16'))
            get.assert_not_called();post.assert_not_called()
        self.assertEqual(result['counts'],{})
        self.assertEqual(result['real_orders_sent'],0)
        self.assertFalse(result['forward_validation_pass'])

    def test_worker_paper_check_runs_even_if_new_screen_fails(self):
        import swing_worker as worker
        (self.root/'exchange_sessions.json').write_text(json.dumps(calendar('2026-10-16')))
        with patch.dict('os.environ',{},clear=True),patch.object(worker.subprocess,'run',side_effect=[Mock(returncode=1),Mock(returncode=0),Mock(returncode=0)]) as process:
            state=worker.tick(self.root,clock('2026-10-16'))
            names=[Path(call.args[0][1]).name for call in process.call_args_list]
            self.assertIn('swing_paper_monitor.py',names)
            worker.tick(self.root,clock('2026-10-16'))
            self.assertEqual(process.call_count,3)
        self.assertEqual(state['entry_alerts'],'BLOCKED_PENDING_LIVE_AND_FORWARD_VALIDATION')
        self.assertIn('2026-10-16',json.loads((self.root/'runtime'/'calendar_history.json').read_text()))


if __name__=='__main__':unittest.main()
