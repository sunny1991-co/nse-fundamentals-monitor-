import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from swing_pullback_research import pullback_features, compare, PROTOCOL


class PullbackResearchTests(unittest.TestCase):
    def frame(self, symbol='TEST', start='2020-01-01', count=1400):
        rng=np.random.default_rng(42)
        c=100*np.exp(np.cumsum(rng.normal(.001,.018,count)))
        return pd.DataFrame(dict(Symbol=symbol,Date=pd.date_range(start,periods=count),
            Open=c,High=c*1.015,Low=c*.985,Close=c,Volume=[1000000.]*count))

    def test_input_not_mutated_and_future_does_not_change_signals(self):
        raw=self.frame();saved=raw.copy(deep=True)
        a=pullback_features(raw.iloc[:900]);b=pullback_features(raw)
        pd.testing.assert_frame_equal(raw,saved)
        pd.testing.assert_frame_equal(a,b.iloc[:900].reset_index(drop=True))

    def test_reclaim_requires_previous_close_at_or_below_ema20(self):
        from swing_strategy import ema
        d=pullback_features(self.frame());e=ema(d.Close,20)
        self.assertTrue((d.loc[d.Signal,'Close']>e[d.Signal]).all())
        self.assertTrue((d.Close.shift()[d.Signal]<=e.shift()[d.Signal]).all())
        self.assertGreater(int(d.Signal.sum()),0)

    def test_sample_independent_of_row_order_and_never_live(self):
        raw=pd.concat([self.frame('AAA'),self.frame('BBB')],ignore_index=True)
        # Production history reader sorts rows; reverse symbol groups only.
        stats=dict(Completed_Trades=0,Trades=0,Mean_Return=0.,Max_Drawdown=0.)
        with patch('swing_pullback_research.simulate',return_value=(stats,[])):
            a=compare(raw,'test-digest',1)
            b=compare(pd.concat([self.frame('BBB'),self.frame('AAA')]),'test-digest',1)
        self.assertEqual(a['sample_symbols'],b['sample_symbols'])
        self.assertEqual(a['eligible_universe'],2)
        self.assertFalse(a['live_eligible'])
        self.assertEqual(a['validation_status'],'NOT_VALIDATED')
        self.assertNotIn('Backtest_Pass',str(a))

    def test_post_design_bars_are_excluded(self):
        raw=self.frame(start='2026-10-16')
        with patch('swing_pullback_research.simulate') as run:
            result=compare(raw,'future',20)
        self.assertEqual(result['eligible_universe'],0)
        run.assert_not_called()

    def test_bounds(self):
        for n in [0,101,-1]:
            with self.assertRaises(ValueError):compare(self.frame(),'x',n)

    def test_real_comparison_keeps_research_separate(self):
        result=compare(self.frame(),'synthetic-test-only',1)
        self.assertEqual(len(result['stock_observations']),2)
        self.assertEqual(set(result['summary']),{'breakout-v1',PROTOCOL['version']})
        self.assertFalse(result['live_eligible'])
        self.assertTrue(all(r['completed_trades']<=r['signal_opportunities'] for r in result['stock_observations']))
