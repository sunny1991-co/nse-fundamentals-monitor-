import unittest
from datetime import datetime,timezone,timedelta
from research_monitor.risk import quote_errors,size_plan
from research_monitor.ipo import review_ipo
from research_monitor.contracts import validate_contract

class ResearchTests(unittest.TestCase):
    def test_contract_unknown_is_blocked(self):
        self.assertEqual(validate_contract({'instrument_type':'OPTIDX'})['status'],'BLOCKED')
    def test_stock_settlement_and_ban_required(self):
        r=validate_contract({'instrument_type':'OPTSTK'})
        self.assertIn('STOCK_SETTLEMENT_OR_BAN_REVIEW_PENDING',r['reasons'])
    def test_ipo_unknown_does_not_pass(self):
        r=review_ipo({'company':'TEST'})
        self.assertEqual(r['status'],'BLOCKED')
        self.assertEqual(r['backtest'],'NOT_POSSIBLE_BEFORE_LISTING')
    def test_old_quote_blocked(self):
        now=datetime.now(timezone.utc)
        q=dict(feed_time=(now-timedelta(seconds=60)).isoformat(),bid=100,ask=100.1,ltp=100)
        self.assertIn('STALE_OR_FUTURE_QUOTE',quote_errors(q,dict(max_quote_age_seconds=15,max_spread_fraction=.005),now))
    def test_missing_capital_blocks(self):
        q=dict(feed_time=datetime.now(timezone.utc).isoformat(),bid=100,ask=100.1,ltp=100)
        r=size_plan({},q,dict(max_quote_age_seconds=15,max_spread_fraction=.005),{})
        self.assertEqual(r['status'],'BLOCKED');self.assertEqual(r['quantity'],0)
    def test_equity_evidence_cannot_approve_options(self):
        q=dict(feed_time=datetime.now(timezone.utc).isoformat(),bid=100,ask=100.1,ltp=100)
        r=size_plan(dict(kind='long_option',evidence={'fundamentals_pass':True}),q,dict(max_quote_age_seconds=15,max_spread_fraction=.005),{})
        self.assertIn('MISSING_EVIDENCE_ACTUAL_OPTION_CONTRACT_BACKTEST',r['reasons'])

if __name__=='__main__': unittest.main()
