"""Synthetic, causal, non-trading tests for frozen trend/RS breakout."""
from __future__ import annotations

from unittest.mock import patch
import math
import tempfile
import unittest

import numpy as np
import pandas as pd

from research_monitor.quality_leader_rules import (
    PARAMETERS,STRATEGY_ID,_check_benchmark,
    quality_leader_features,model_plan_at_close,
)
from swing_quality_leader_research import compare,PROTOCOL


def synthetic(count=620, symbol="SYNTH"):
    dates=pd.bdate_range("2023-01-02",periods=count)
    price=100.0+.40*np.arange(count,dtype=float)
    price[-1]+=8.0
    volume=np.full(count,2_000_000.0)
    volume[-1]=4_000_000.0
    stock=pd.DataFrame({
        "Symbol":[symbol]*count,"Date":dates,
        "Open":price-0.5,
        "High":price+0.8,
        "Low":price-1.2,
        "Close":price,
        "Volume":volume,
    })
    stock.loc[count-1,"High"]=price[-1]+.35
    stock.loc[count-1,"Low"]=price[-1]-2.
    benchmark=pd.Series(
        100.0+.08*np.arange(count,dtype=float),index=dates,
        name="NIFTY_50_TRI",
    )
    return stock,benchmark


def fixed_indicators(raw):
    d=raw.reset_index(drop=True).copy()
    d["ATR"]=5.0
    d["RSI"]=64.0
    d["ADX"]=25.0
    d["Stop"]=d.Close-10.
    d["Signal"]=False
    return d


class FrozenLeaderRulesTests(unittest.TestCase):
    def test_constructed_quality_leader_breakout_and_plan_has_no_trade_permission(self):
        bars,tri=synthetic()
        with patch("research_monitor.quality_leader_rules.features",
                   side_effect=fixed_indicators):
            d=quality_leader_features(bars,tri)
        self.assertTrue(bool(d.Signal.iloc[-1]))
        self.assertFalse(bool(d.Signal.iloc[-2]))
        self.assertTrue(d.TRI_Exact_Day_Present.iloc[-1])
        self.assertGreater(d.Relative_TRI_63d_Return.iloc[-1],.05)
        plan=model_plan_at_close(d)
        self.assertEqual(plan["setup"],STRATEGY_ID)
        self.assertLess(plan["indicative_initial_stop"],
                        plan["hypothetical_reference_entry_at_signal_close"])
        self.assertEqual(plan["max_holding_sessions"],100)
        self.assertEqual(plan["position_risk_fraction_model"],.01)
        self.assertIsNone(plan["win_probability"])
        self.assertFalse(plan["live_eligible"])
        self.assertFalse(plan["fundamental_point_in_time_verified"])
        self.assertEqual(plan["orders"],"DISABLED")

    def test_missing_or_stale_tri_day_not_forward_filled_to_trigger_signal(self):
        bars,tri=synthetic()
        with patch("research_monitor.quality_leader_rules.features",
                   side_effect=fixed_indicators):
            d=quality_leader_features(bars,tri.iloc[:-1])
        self.assertFalse(bool(d.Signal.iloc[-1]))
        self.assertFalse(bool(d.TRI_Exact_Day_Present.iloc[-1]))
        self.assertTrue(math.isnan(d.Relative_TRI_63d_Return.iloc[-1]))

    def test_future_stock_or_benchmark_appended_does_not_rewrite_past_signals(self):
        bars,tri=synthetic(700)
        with patch("research_monitor.quality_leader_rules.features",
                   side_effect=fixed_indicators):
            earlier=quality_leader_features(bars.iloc[:620],tri.iloc[:620])
            later=quality_leader_features(bars,tri)
        pd.testing.assert_frame_equal(
            earlier,later.iloc[:620].reset_index(drop=True)
        )
        self.assertFalse(earlier.Signal.iloc[0:259].any())

    def test_bearish_market_disables_even_strong_stock_breakout(self):
        bars,tri=synthetic()
        bearish=pd.Series(
            190.-.12*np.arange(len(tri)),index=tri.index
        )
        with patch("research_monitor.quality_leader_rules.features",
                   side_effect=fixed_indicators):
            d=quality_leader_features(bars,bearish)
        self.assertFalse(bool(d.Signal.iloc[-1]))

    def test_no_chase_and_next_session_fill_risk_model_are_frozen(self):
        self.assertEqual(PARAMETERS["max_next_session_entry_gap_above_signal_close"],.01)
        self.assertEqual(PARAMETERS["max_holding_sessions"],100)
        self.assertEqual(PARAMETERS["max_initial_stop_pct"],.10)
        self.assertEqual(PARAMETERS["illustrative_risk_per_independent_stock_account"],.01)
        self.assertFalse(PROTOCOL["live_trading"])

    def test_invalid_benchmark_or_candles_fail_closed(self):
        bars,tri=synthetic()
        with self.assertRaisesRegex(ValueError,"NIFTY_50_TRI_DATES_INVALID"):
            _check_benchmark(tri.iloc[::-1])
        with self.assertRaisesRegex(ValueError,"NIFTY_50_TRI_LEVELS_INVALID"):
            _check_benchmark(tri*float("nan"))
        dup=bars.copy()
        dup.loc[1,"Date"]=dup.loc[0,"Date"]
        with self.assertRaisesRegex(ValueError,"OHLCV_CHRONOLOGY_INVALID"):
            quality_leader_features(dup,tri)
        impossible=bars.copy()
        impossible.loc[0,"High"]=impossible.loc[0,"Close"]-20
        with self.assertRaisesRegex(ValueError,"OHLCV_PRICES_INVALID"):
            quality_leader_features(impossible,tri)

    def test_retrospective_comparison_uses_next_open_fee_and_stress_simulator_only(self):
        bars,tri=synthetic(1380)
        calls=[]
        def simulator(d,start,end,**kwargs):
            calls.append(kwargs)
            return ({
                "Max_Drawdown":.03,"Completed_Trades":1,
            },[{
                "Reason":"stop","Return":-.04,
                "Gap_Below_Stop":True,
                "Risk_Budget_Exceeded_On_Exit":True,
            }])
        with patch("swing_quality_leader_research.quality_leader_features") as strat, \
             patch("swing_quality_leader_research.features") as old:
            frame=fixed_indicators(bars)
            frame["Signal"]=False
            strat.return_value=frame
            old.return_value=frame
            result=compare(bars,"synthetic_history_digest",tri,
                           "synthetic_benchmark_digest",limit=1,
                           simulator=simulator)
        self.assertEqual(len(calls),4)
        self.assertEqual(sorted(x["stress_multiplier"] for x in calls),
                         [1.,1.,2.,2.])
        self.assertEqual(sorted(x["slip"] for x in calls),[.001,.001,.002,.002])
        self.assertTrue(all(x["max_hold"]==100 for x in calls))
        self.assertTrue(all(x["risk_fraction"]==.01 for x in calls))
        self.assertEqual(result["summary"][STRATEGY_ID]["completed_trades"],1)
        self.assertEqual(result["summary"][STRATEGY_ID]["gap_below_stop_exits"],1)
        self.assertEqual(result["summary"][STRATEGY_ID]["risk_budget_breaches"],1)
        self.assertEqual(result["summary"][STRATEGY_ID]["mean_completed_net_position_return"],-.04)
        self.assertFalse(result["independent_holdout_pass"])
        self.assertFalse(result["forward_paper_track_record_available"])
        self.assertFalse(result["strategy_validated_profitable"])
        self.assertFalse(result["live_eligible"])
        self.assertEqual(result["groww_orders"],"DISABLED")

    def test_retrospective_frozen_end_and_no_tuning_future_data(self):
        bars,tri=synthetic(1440)
        eligible=bars.iloc[:1320]
        newer=bars.iloc[1320:].copy()
        newer["Date"]=pd.date_range("2026-10-16",periods=len(newer),freq="B")
        extended=pd.concat([eligible,newer],ignore_index=True)
        tri_old=tri.iloc[:1320]
        tri_new=pd.Series(
            np.linspace(300,200,len(newer)),
            index=pd.DatetimeIndex(newer.Date)
        )
        complete_tri=pd.concat([tri_old,tri_new]).sort_index()
        def simulator(d,start,end,**kwargs):
            return ({"Max_Drawdown":0.},[])
        with patch("swing_quality_leader_research.quality_leader_features",
                   side_effect=lambda b,t:fixed_indicators(b)), \
             patch("swing_quality_leader_research.features",
                   side_effect=fixed_indicators):
            a=compare(eligible,"historical",tri,"benchmark",1,
                      simulator=simulator)
            b=compare(extended,"historical",complete_tri,"benchmark",1,
                      simulator=simulator)
        self.assertEqual(a["summary"],b["summary"])
        self.assertEqual(a["per_stock_observations"],b["per_stock_observations"])


if __name__=="__main__":
    unittest.main()
