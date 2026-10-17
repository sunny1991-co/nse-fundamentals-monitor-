"""Causal, no-lookahead and no-broker tests for RS Pullback Recovery."""
from unittest.mock import patch
import math
import unittest

import numpy as np
import pandas as pd

from research_monitor.rs_pullback_rules import (
    PARAMETERS, STRATEGY_ID, rs_pullback_features, illustrative_plan,
)
from swing_rs_pullback_research import compare, PROTOCOL


def synthetic(n=680):
    dates = pd.bdate_range("2020-01-01", periods=n)
    close = 100 + .4*np.arange(n,dtype=float)
    close[-4:-1] -= 8
    close[-1] += 1.
    open_px = close - .4
    volume = np.full(n,2_000_000.0)
    volume[-5:-1] = 1_000_000.0
    volume[-1] = 4_000_000.0
    bars = pd.DataFrame({
        "Symbol":["SYNTH"]*n, "Date":dates, "Open":open_px,
        "High":close+.8, "Low":close-1.5, "Close":close,
        "Volume":volume,
    })
    tri = pd.Series(100.+.06*np.arange(n),index=dates)
    return bars, tri


def stable_indicators(data):
    d=data.reset_index(drop=True).copy()
    d["ATR"] = 4.5
    d["RSI"] = 60.
    d["ADX"] = 25.
    d["Stop"] = d.Close - 9.
    d["Signal"] = False
    return d


class RSPullbackHypothesisTests(unittest.TestCase):
    def test_shallow_pullback_then_recovery_creates_no_live_order(self):
        bars,tri=synthetic()
        with patch("research_monitor.rs_pullback_rules.features",
                   side_effect=stable_indicators):
            d=rs_pullback_features(bars,tri)
        self.assertTrue(bool(d.Signal.iloc[-1]))
        self.assertFalse(bool(d.Signal.iloc[-2]))
        self.assertGreater(float(d.Relative_TRI_126d_Return.iloc[-1]),.08)
        plan=illustrative_plan(d)
        self.assertEqual(plan["strategy"],STRATEGY_ID)
        self.assertGreater(plan["indicative_target2_at_close_plus_3r"],plan["reference_close"])
        self.assertLess(plan["indicative_stop"],plan["reference_close"])
        self.assertFalse(plan["live_eligible"])
        self.assertIsNone(plan["win_probability"])
        self.assertEqual(plan["orders"],"DISABLED")

    def test_missing_tri_exact_day_cannot_signal_even_strong_stock(self):
        bars,tri=synthetic()
        with patch("research_monitor.rs_pullback_rules.features",
                   side_effect=stable_indicators):
            d=rs_pullback_features(bars,tri.iloc[:-1])
        self.assertFalse(bool(d.Signal.iloc[-1]))
        self.assertFalse(bool(d.TRI_Exact_Day_Present.iloc[-1]))
        self.assertTrue(math.isnan(float(d.Relative_TRI_126d_Return.iloc[-1])))

    def test_missing_benchmark_session_inside_lookback_blocks_signal(self):
        bars,tri=synthetic()
        tri=tri.drop(tri.index[-45])
        with patch("research_monitor.rs_pullback_rules.features",
                   side_effect=stable_indicators):
            d=rs_pullback_features(bars,tri)
        self.assertTrue(bool(d.TRI_Exact_Day_Present.iloc[-1]))
        self.assertFalse(bool(d.TRI_Complete_260_Sessions.iloc[-1]))
        self.assertFalse(bool(d.Signal.iloc[-1]))

    def test_future_appended_data_cannot_rewrite_past_signals_or_stops(self):
        bars,tri=synthetic(740)
        with patch("research_monitor.rs_pullback_rules.features",
                   side_effect=stable_indicators):
            past=rs_pullback_features(bars.iloc[:680],tri.iloc[:680])
            later=rs_pullback_features(bars,tri)
        pd.testing.assert_frame_equal(past,later.iloc[:680].reset_index(drop=True))
        self.assertFalse(past.Signal.iloc[:259].any())

    def test_bear_market_blocks_pullback(self):
        bars,tri=synthetic()
        tri=pd.Series(200.-.10*np.arange(len(tri)),index=tri.index)
        with patch("research_monitor.rs_pullback_rules.features",
                   side_effect=stable_indicators):
            d=rs_pullback_features(bars,tri)
        self.assertFalse(bool(d.Signal.iloc[-1]))

    def test_frozen_risk_and_no_chase_parameters(self):
        self.assertEqual(PARAMETERS["entry_gap_max_above_signal_close"],.01)
        self.assertEqual(PARAMETERS["max_holding_sessions"],100)
        self.assertEqual(PARAMETERS["risk_fraction_per_independent_paper_account"],.01)
        self.assertEqual(PROTOCOL["orders"],"DISABLED")
        self.assertFalse(PROTOCOL["comparison_is_new_holdout"])

    def test_bogus_price_or_duplicate_dates_fails_shut(self):
        bars,tri=synthetic()
        bad=bars.copy()
        bad.loc[0,"Low"]=bad.loc[0,"High"]+1
        with self.assertRaisesRegex(ValueError,"OHLCV_PRICES_INVALID"):
            rs_pullback_features(bad,tri)
        duplicate=bars.copy()
        duplicate.loc[5,"Date"]=duplicate.loc[4,"Date"]
        with self.assertRaisesRegex(ValueError,"OHLCV_CHRONOLOGY_INVALID"):
            rs_pullback_features(duplicate,tri)

    def test_matched_three_way_retro_backtest_cost_and_gap_attribution(self):
        bars,tri=synthetic(1350)
        calls=[]
        def simulator(d,start,end,**kwargs):
            calls.append(kwargs)
            return ({"Max_Drawdown":.045},[{
                "Reason":"stop", "Return":-.025,
                "Gap_Below_Stop":True,
                "Risk_Budget_Exceeded_On_Exit":True,
            }])
        with patch("swing_rs_pullback_research.rs_pullback_features",
                   side_effect=lambda d,_:stable_indicators(d)), \
             patch("swing_rs_pullback_research.quality_leader_features",
                   side_effect=lambda d,_:stable_indicators(d)), \
             patch("swing_rs_pullback_research.features",
                   side_effect=stable_indicators):
            result=compare(bars,"synthetic_history",tri,"synthetic_tri",
                           limit=1,simulator=simulator)
        self.assertEqual(len(calls),6)
        self.assertEqual(sorted(p["slip"] for p in calls),[.001]*3+[.002]*3)
        self.assertEqual(sorted(p["stress_multiplier"] for p in calls),
                         [1.]*3+[2.]*3)
        self.assertTrue(all(p["risk_fraction"]==.01 for p in calls))
        self.assertTrue(all(p["max_hold"]==100 for p in calls))
        self.assertEqual(result["comparison"][STRATEGY_ID]["completed_trades"],1)
        self.assertEqual(result["comparison"][STRATEGY_ID]["gap_below_stop_exits"],1)
        self.assertTrue(result["no_new_independent_holdout"])
        self.assertFalse(result["historical_point_in_time_fundamentals_verified"])
        self.assertFalse(result["strategy_profitability_validated"])
        self.assertFalse(result["live_eligible"])
        self.assertEqual(result["trading_orders"],"DISABLED")

    def test_new_future_days_cannot_change_frozen_past_research(self):
        bars,tri=synthetic(1330)
        early=bars.copy()
        early["Date"]=pd.bdate_range(end="2026-10-15",periods=len(bars))
        tri.index=pd.DatetimeIndex(early.Date)
        extra=early.iloc[-1:].copy()
        extra["Date"]=pd.Timestamp("2026-11-04")
        future=pd.concat([early,extra],ignore_index=True)
        future_tri=pd.concat([
            tri,pd.Series([tri.iloc[-1]*1.02],index=pd.DatetimeIndex(["2026-11-04"]))
        ])
        def simulator(d,start,end,**kwargs):
            return {"Max_Drawdown":.0},[]
        with patch("swing_rs_pullback_research.rs_pullback_features",
                   side_effect=lambda d,_:stable_indicators(d)), \
             patch("swing_rs_pullback_research.quality_leader_features",
                   side_effect=lambda d,_:stable_indicators(d)), \
             patch("swing_rs_pullback_research.features",
                   side_effect=stable_indicators):
            old=compare(early,"fixed",tri,"fixedtri",limit=1,simulator=simulator)
            new=compare(future,"fixed",future_tri,"fixedtri",limit=1,simulator=simulator)
        self.assertEqual(old["comparison"],new["comparison"])
        self.assertEqual(old["per_stock_research"],new["per_stock_research"])


if __name__=="__main__":
    unittest.main()
