import unittest
import pandas as pd
from swing_signal_audit import entry_audit
from swing_backtest import simulate


class SignalAuditTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame(dict(Date=pd.date_range('2020-01-01',periods=225),
            Open=[100.]*225,High=[101.]*225,Low=[99.]*225,Close=[100.]*225,
            Stop=[95.]*225,ATR=[1.]*225,Signal=[False]*225))

    def test_previous_signal_and_last_unfillable_signal(self):
        d=self.frame();d.loc[219,'Signal']=True;d.loc[224,'Signal']=True
        result=entry_audit(d,220,225,slip=0)
        self.assertEqual(result['signal_opportunities'],1)
        self.assertEqual(result['eligible_entry_upper_bound'],1)

    def test_entry_rejections_match_price_and_risk_rules(self):
        d=self.frame();d.loc[219:223,'Signal']=True
        d.loc[220,'Open']=99;d.loc[221,'Open']=102;d.loc[221,'Stop']=80
        # The stop at signal row 221 applies to entry row 222.
        result=entry_audit(d,220,225,slip=0)
        self.assertEqual(result['below_signal_close'],1)
        self.assertEqual(result['above_one_percent_band'],1)
        self.assertEqual(result['invalid_or_excess_stop_distance'],1)
        self.assertEqual(result['eligible_entry_upper_bound'],2)

    def test_upper_bound_includes_signals_while_position_is_open(self):
        d=self.frame();d.loc[219:223,'Signal']=True
        result=entry_audit(d,220,225)
        stats,_=simulate(d,220,225)
        self.assertEqual(result['eligible_entry_upper_bound'],5)
        self.assertEqual(stats['Trades'],1)
        self.assertGreaterEqual(result['eligible_entry_upper_bound'],stats['Trades'])

    def test_slippage_can_reject_nominal_one_percent_open(self):
        d=self.frame();d.loc[219,'Signal']=True;d.loc[220,'Open']=101
        self.assertEqual(entry_audit(d,220,225)['above_one_percent_band'],1)
