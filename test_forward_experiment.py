import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from research_monitor import forward_experiment as f
from research_monitor import paper


class ForwardExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026,10,19,12,tzinfo=timezone.utc)
        self.raw = pd.DataFrame(dict(Symbol=['TEST']*1260,
            Date=pd.date_range(end='2026-10-19',periods=1260),
            Open=[100.]*1260,High=[102.]*1260,Low=[99.]*1260,
            Close=[101.]*1260,Volume=[1000000]*1260,Stop=[95.]*1260))
        self.cal = {'2026-10-19':dict(verified=True,source='test-fixture',
            valid_from='2026-10-19',valid_to='2026-10-19',
            sessions={'2026-10-19':dict(open='2026-10-19T09:15:00+05:30',
                                      close='2026-10-19T15:30:00+05:30')})}

    def register(self, started):
        with patch.object(f,'features',side_effect=lambda x:x), patch.object(f,'signals',return_value=pd.Series([True]*1260)):
            return f.register(self.root,self.raw,self.cal,self.now,started)

    def test_no_backfill_on_enrollment(self):
        self.assertEqual(self.register(self.now),0)

    def test_three_frozen_signals_idempotent(self):
        started=self.now-timedelta(days=1)
        self.assertEqual(self.register(started),3)
        self.assertEqual(self.register(started),0)

    def test_missing_calendar_never_registers(self):
        self.cal={}
        self.assertEqual(self.register(self.now-timedelta(days=1)),0)

    def test_incomplete_session_never_registers(self):
        self.now=self.now.replace(hour=8)
        self.assertEqual(self.register(self.now-timedelta(days=1)),0)

    def test_today_plan_not_marked_revised(self):
        self.register(self.now-timedelta(days=1))
        paper.reconcile(self.root,self.raw.iloc[:0],self.cal,self.now)
        with paper.connect(self.root) as db:
            states=[x[0] for x in db.execute('SELECT status FROM plans')]
        self.assertEqual(states,['WAITING_FOR_COMPLETED_SESSION']*3)

    def test_protocol_change_blocks(self):
        with patch.object(f,'read_history',return_value=(self.raw,'test')):
            first=f.run(self.root,self.now)
        self.assertEqual(first['new_registered'],0)
        with patch.object(f,'code_hash',return_value='changed'):
            second=f.run(self.root,self.now)
        self.assertEqual(second['state'],'BLOCKED')
        self.assertIn('FROZEN_CODE_CHANGED',second['reason'])
        self.assertFalse(second['live_eligible'])

if __name__=='__main__':
    unittest.main()
