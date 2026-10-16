"""Synthetic invariants only, NOT NSE equity strategy profitability."""
from __future__ import annotations

import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

from research_monitor.regime_momentum_rules import (
    STRATEGY_ID, PARAMETERS, make_market_context, regime_features,
)
from swing_regime_momentum_research import compare, COMPARATOR


def ohlcv(symbol="TEST", n=1320):
    rng=np.random.default_rng(20261016)
    c=100*np.exp(np.cumsum(rng.normal(.0008,.009,n)))
    return pd.DataFrame({
        "Date":pd.date_range("2020-01-01", periods=n),
        "Symbol":[symbol]*n,
        "Open":c.copy(),"High":c*1.013,"Low":c*.987,
        "Close":c,"Volume":np.full(n,1_000_000.),
    })


def manual_context(bars, *, breadth=.7, breadth_stocks=200,
                   momentum_stocks=200, rank=.95):
    dates=pd.DatetimeIndex(bars.Date)
    market=pd.DataFrame({
        "Breadth": [breadth]*len(bars),
        "Breadth_Stocks": [breadth_stocks]*len(bars),
        "Momentum_Stocks": [momentum_stocks]*len(bars),
    },index=dates)
    ranks=pd.Series(
        [rank]*len(bars),
        index=pd.MultiIndex.from_arrays(
            [[bars.Symbol.iloc[0]]*len(bars),dates],
            names=["Symbol","Date"],
        ),
        dtype=float
    )
    return market,ranks


class RegimeMomentumTests(unittest.TestCase):
    def test_missing_context_prohibits_signal(self):
        bars=ohlcv()
        market,ranks=manual_context(bars)
        valid=regime_features(bars,"TEST",market,ranks)
        no_market=regime_features(bars,"TEST",market.iloc[:0],ranks)
        no_ranks=regime_features(
            bars,"TEST",market,pd.Series(dtype=float)
        )
        low_coverage=regime_features(
            bars,"TEST",
            manual_context(bars,breadth_stocks=99)[0],ranks
        )
        self.assertFalse(no_market.Signal.any())
        self.assertFalse(no_ranks.Signal.any())
        self.assertFalse(low_coverage.Signal.any())
        self.assertEqual(valid.Signal.dtype,bool)

    def test_no_future_bar_can_change_previous_signals(self):
        bars=ohlcv()
        market,ranks=manual_context(bars)
        full=regime_features(bars,"TEST",market,ranks)
        earlier=regime_features(bars.iloc[:900],"TEST",market,ranks)
        pd.testing.assert_series_equal(
            earlier.Signal,full.Signal.iloc[:900].reset_index(drop=True)
        )

    def test_nontrading_day_is_not_forward_filled(self):
        bars=ohlcv()
        market,ranks=manual_context(bars)
        start=regime_features(bars,"TEST",market,ranks)
        removed=pd.Timestamp(bars.Date.iloc[1000])
        market2=market.drop(index=removed)
        after=regime_features(bars,"TEST",market2,ranks)
        self.assertFalse(bool(after.Signal.iloc[1000]))
        self.assertEqual(
            int(start.Signal.iloc[:1000].sum()),
            int(after.Signal.iloc[:1000].sum()),
        )

    def test_cross_section_ranking_uses_same_date_price_only(self):
        a,b=ohlcv("AAA"),ohlcv("BBB")
        b=b.copy()
        b["Close"] *= (1+np.linspace(0,.5,len(b)))
        b["Open"] *= (1+np.linspace(0,.5,len(b)))
        b["High"] *= (1+np.linspace(0,.5,len(b)))
        b["Low"] *= (1+np.linspace(0,.5,len(b)))
        breadth,ranks=make_market_context({"AAA":a,"BBB":b})
        self.assertEqual(int(breadth.Breadth_Stocks.iloc[-1]),2)
        day=pd.Timestamp(a.Date.iloc[-1])
        self.assertGreater(
            ranks.loc[("BBB",day)], ranks.loc[("AAA",day)]
        )
        # A later date cannot change the momentum rank or breadth of day.
        future=b.iloc[-1:].copy()
        future.loc[:,"Date"]=b.Date.iloc[-1]+pd.Timedelta(days=1)
        future.loc[:,"Close"]=b.Close.iloc[-1]*10
        future.loc[:,"Open"]=future.Close.iloc[0]
        future.loc[:,"High"]=future.Close.iloc[0]*1.02
        future.loc[:,"Low"]=future.Close.iloc[0]*.98
        bread2,ranks2=make_market_context({
            "AAA":a, "BBB":pd.concat([b,future])
        })
        self.assertAlmostEqual(
            float(breadth.loc[day,"Breadth"]),
            float(bread2.loc[day,"Breadth"])
        )
        self.assertEqual(ranks.loc[("BBB",day)],ranks2.loc[("BBB",day)])

    def test_momentum_thresholds_are_frozen_rules(self):
        self.assertEqual(PARAMETERS["lookback_momentum"],126)
        self.assertEqual(PARAMETERS["min_breadth_stocks"],100)
        self.assertEqual(PARAMETERS["min_momentum_percentile"],.70)
        self.assertLessEqual(PARAMETERS["max_stop_distance"],.10)

    def test_fee_model_and_risk_fraction_are_both_applied(self):
        dummy={
            "Completed_Trades":0,"Max_Drawdown":0.,
            "Estimated_All_Trading_Charges_Rs":0.,
            "Final_Marked_Capital_Rs":100000.,
        }
        stock=ohlcv()
        with patch("swing_regime_momentum_research.simulate_delivery",
                   return_value=(dummy,[])) as sim:
            report=compare(stock,"SYNTHETIC_DIGEST",1)
        self.assertEqual(sim.call_count,4)
        self.assertEqual(set(report["summary"]),{STRATEGY_ID,COMPARATOR})
        for call in sim.call_args_list:
            self.assertEqual(call.kwargs["capital"],100000.)
            self.assertEqual(call.kwargs["risk_fraction"],.01)
            self.assertIn(call.kwargs["stress_multiplier"],(1.,2.))
        self.assertFalse(report["live_eligible"])
        self.assertFalse(report["approved_for_current_signals"])
        self.assertEqual(report["trade_execution"],"DISABLED")
        self.assertEqual(report["verified_profitable_strategies"],0)
        self.assertEqual(
            report["validation_status"],"NOT_VALIDATED"
        )

    def test_after_design_bars_excluded_from_history_comparison(self):
        a=ohlcv()
        future=a.iloc[-1:].copy()
        future.loc[:,"Date"]=pd.Timestamp("2026-10-16")
        future.loc[:,"Close"]=1e7
        future.loc[:,"Open"]=1e7
        future.loc[:,"High"]=1e7+10
        future.loc[:,"Low"]=1e7-10
        dummy={
            "Completed_Trades":0,"Max_Drawdown":0.,
            "Estimated_All_Trading_Charges_Rs":0.,
            "Final_Marked_Capital_Rs":100000.,
        }
        with patch("swing_regime_momentum_research.simulate_delivery",
                   return_value=(dummy,[])):
            first=compare(a,"TEST",1)
            next_day=compare(pd.concat([a,future]),"TEST",1)
        self.assertEqual(first["summary"],next_day["summary"])
        self.assertEqual(
            first["stock_observations"],next_day["stock_observations"]
        )

    def test_out_of_bounds_limit_rejected(self):
        with self.assertRaises(ValueError):
            compare(ohlcv(),"TEST",0)
        with self.assertRaises(ValueError):
            compare(ohlcv(),"TEST",101)
        with self.assertRaises(ValueError):
            compare(ohlcv(),"TEST",True)


if __name__=="__main__":
    unittest.main()
