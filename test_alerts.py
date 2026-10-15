import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from research_monitor.alerts import prepare_alert

class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'outbox.sqlite'
        self.now=datetime.now(timezone.utc)
        self.event=dict(module='SWING',type='ENTRY_SETUP',event_id='test-event',instrument='TEST',strategy_version='test-v1',
            reason='Synthetic test only',entry_min=100,entry_max=101,stop_loss=95,targets=[110,115],
            risk_note='Test',data_as_of=self.now.isoformat(),expires_at=(self.now+timedelta(seconds=30)).isoformat(),
            paper_summary='Synthetic',backtest_summary='Synthetic',backtest_validated=True,
            forward_paper_validated=True,current_setup_validated=True,risk_checks_passed=True)
    def tearDown(self): self.tmp.cleanup()
    def test_dedup(self):
        self.assertEqual(prepare_alert(self.path,self.event,self.now)['status'],'QUEUED_NOT_SENT')
        self.assertEqual(prepare_alert(self.path,self.event,self.now)['status'],'DUPLICATE_SUPPRESSED')
    def test_swing_and_fno_events_are_separate(self):
        first=prepare_alert(self.path,self.event,self.now)
        self.event['module']='FNO'
        second=prepare_alert(self.path,self.event,self.now)
        self.assertEqual(second['status'],'QUEUED_NOT_SENT')
        self.assertNotEqual(first['id'],second['id'])
    def test_expiry_blocks(self):
        self.assertEqual(prepare_alert(self.path,self.event,self.now+timedelta(seconds=40))['status'],'BLOCKED')
    def test_unvalidated_paper_blocks(self):
        self.event['forward_paper_validated']=False
        self.assertEqual(prepare_alert(self.path,self.event,self.now)['status'],'BLOCKED')
    def test_no_invented_real_holdings(self):
        self.event['type']='HOLDING_EXIT'
        self.assertEqual(prepare_alert(self.path,self.event,self.now)['status'],'BLOCKED')

if __name__=='__main__': unittest.main()
