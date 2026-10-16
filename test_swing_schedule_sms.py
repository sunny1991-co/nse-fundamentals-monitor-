import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock
import requests
from research_monitor.schedule import phase, claim_job
from research_monitor.nse_calendar import official_equity_calendar
from research_monitor.sms import deliver
from research_monitor.alerts import prepare_alert

class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.calendar = dict(verified=True, source='synthetic exchange calendar', valid_from='2026-10-01', valid_to='2026-10-31', sessions={'2026-10-15':{'open':'2026-10-15T09:15:00+05:30','close':'2026-10-15T15:30:00+05:30'}})
    def test_boundaries(self):
        for clock, expected in [('09:14:59','PRE_MARKET'),('09:15:00','LIVE_MONITORING'),('15:29:59','LIVE_MONITORING'),('15:30:00','AFTER_MARKET_ANALYSIS')]:
            self.assertEqual(phase(self.calendar,datetime.fromisoformat('2026-10-15T'+clock+'+05:30')),expected)
    def test_fail_closed_calendar_and_holidays(self):
        self.assertEqual(phase({},datetime.now(timezone.utc)),'CALENDAR_PENDING')
        self.assertEqual(phase(self.calendar,datetime.fromisoformat('2026-10-16T10:00:00+05:30')),'NON_TRADING_DAY')
        self.assertEqual(phase(self.calendar,datetime.fromisoformat('2026-11-01T10:00:00+05:30')),'CALENDAR_PENDING')
    def test_reviewed_official_nse_calendar_without_broker(self):
        calendar = official_equity_calendar(2026)
        self.assertTrue(calendar['verified'])
        self.assertEqual(
            phase(calendar, datetime.fromisoformat('2026-10-16T10:00:00+05:30')),
            'LIVE_MONITORING',
        )
        self.assertEqual(
            phase(calendar, datetime.fromisoformat('2026-10-20T10:00:00+05:30')),
            'NON_TRADING_DAY',
        )

    def test_special_session_pending_and_unknown_year_fail_closed(self):
        calendar = official_equity_calendar(2026)
        self.assertEqual(
            phase(calendar, datetime.fromisoformat('2026-11-08T18:00:00+05:30')),
            'CALENDAR_PENDING',
        )
        self.assertEqual(
            phase(official_equity_calendar(2027), datetime.fromisoformat('2027-01-04T10:00:00+05:30')),
            'CALENDAR_PENDING',
        )

    def test_restart_does_not_repeat_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'jobs.sqlite'
            self.assertTrue(claim_job(path,'2026-10-15','eod'))
            self.assertFalse(claim_job(path,'2026-10-15','eod'))

class SmsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'outbox.sqlite';self.now=datetime.now(timezone.utc)
        self.event=dict(module='SWING',type='ENTRY_SETUP',event_id='a',instrument='TEST',strategy_version='v1',reason='synthetic',entry_min=100,entry_max=101,stop_loss=95,targets=[110,115],risk_note='test',data_as_of=self.now.isoformat(),expires_at=(self.now+timedelta(seconds=60)).isoformat(),paper_summary='test',backtest_summary='test',backtest_validated=True,forward_paper_validated=True,current_setup_validated=True,risk_checks_passed=True)
        self.identifier=prepare_alert(self.path,self.event,self.now)['id']
        self.env=dict(SMS_ENABLED='1',SMS_TEMPLATE_APPROVED='1',MSG91_AUTH_KEY='synthetic',SMS_TEMPLATE_ID='synthetic',SMS_RECIPIENT='919999999999')
    def test_disabled_no_network(self):
        post=Mock();self.assertEqual(deliver(self.path,self.identifier,{},self.now,post),'PROVIDER_SETUP_REQUIRED');post.assert_not_called()
    def test_stale_no_network(self):
        post=Mock();self.assertEqual(deliver(self.path,self.identifier,self.env,self.now+timedelta(seconds=16),post),'EXPIRED');post.assert_not_called()
    def test_send_once_provider_acceptance_is_not_delivery(self):
        post=Mock(return_value=Mock(ok=True,json=lambda:{'type':'success'}))
        self.assertEqual(deliver(self.path,self.identifier,self.env,self.now,post),'ACCEPTED_BY_PROVIDER')
        self.assertEqual(deliver(self.path,self.identifier,self.env,self.now,post),'NOT_PENDING');self.assertEqual(post.call_count,1)
    def test_timeout_never_automatically_resends(self):
        post=Mock(side_effect=requests.Timeout())
        self.assertEqual(deliver(self.path,self.identifier,self.env,self.now,post),'DELIVERY_UNKNOWN_DO_NOT_RETRY')
        self.assertEqual(deliver(self.path,self.identifier,self.env,self.now,post),'NOT_PENDING')
    def test_invalid_prices_block(self):
        self.event['stop_loss']=float('nan')
        self.assertEqual(prepare_alert(self.path,self.event,self.now)['status'],'BLOCKED')

if __name__=='__main__': unittest.main()

class UpstoxTests(unittest.TestCase):
    def test_never_calls_network_without_token(self):
        from research_monitor.upstox_data import UpstoxData,DataUnavailable
        from unittest.mock import patch
        with patch.dict('os.environ',{},clear=True):
            get=Mock()
            with self.assertRaises(DataUnavailable): UpstoxData(get=get).is_open()
            get.assert_not_called()
    def test_derivative_keys_rejected(self):
        from research_monitor.upstox_data import UpstoxData,DataUnavailable
        get=Mock()
        with self.assertRaises(DataUnavailable): UpstoxData(token='test',get=get).quotes(['NSE_FO|123'],datetime.now(timezone.utc))
        get.assert_not_called()
    def test_missing_quote_rejected(self):
        from research_monitor.upstox_data import UpstoxData,DataUnavailable
        get=Mock(return_value=Mock(status_code=200,json=lambda:{'status':'success','data':{}}))
        with self.assertRaises(DataUnavailable): UpstoxData(token='test',get=get).quotes(['NSE_EQ|TEST'],datetime.now(timezone.utc))
    def test_holiday_calendar(self):
        from research_monitor.upstox_data import UpstoxData
        get=Mock(return_value=Mock(status_code=200,json=lambda:{'status':'success','data':[]}))
        now=datetime.fromisoformat('2026-10-15T10:00:00+05:30')
        calendar=UpstoxData(token='test',get=get).calendar(now)
        self.assertEqual(phase(calendar,now),'NON_TRADING_DAY')
