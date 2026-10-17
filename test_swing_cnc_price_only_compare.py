"""Synthetic coverage of stock-only CNC research boundaries; no real market alpha."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from swing_cnc_price_only_compare import (
    CAPITAL, END_DATE, PROTOCOL, STRATEGIES, compare, completed,
)


def synthetic(n=1350, *, symbol="SYNTH"):
    dates = pd.bdate_range(end="2026-10-15", periods=n)
    close = 100 + .1*np.arange(n)
    return pd.DataFrame({
        "Symbol": [symbol]*n,
        "Date": dates,
        "Open": close-.2,
        "High": close+1.,
        "Low": close-1.,
        "Close": close,
        "Volume": np.full(n,2_000_000.),
    })


def fake_builder(data):
    d=data.reset_index(drop=True).copy()
    d["Signal"]=False
    d.loc[d.index[-16], "Signal"]=True
    d["Stop"]=d.Close-4.
    d["ATR"]=2.0
    return d


def fake_sim(d, start, end, **kwargs):
    if kwargs["stress_multiplier"] == 1:
        return {"Max_Drawdown":.025},[
            {"Reason":"stop","Return":-.02,
             "Gap_Below_Stop":True, "Risk_Budget_Exceeded_On_Exit":True},
            {"Reason":"sample_end","Return":.5},
        ]
    return {"Max_Drawdown":.04},[
        {"Reason":"stop","Return":-.04,
         "Gap_Below_Stop":True, "Risk_Budget_Exceeded_On_Exit":True},
        {"Reason":"sample_end","Return":.5},
    ]


class PriceOnlyDeliveryComparisonTests(unittest.TestCase):
    def test_uses_three_preexisting_strategies_only(self):
        self.assertEqual([x[0] for x in STRATEGIES], [
            "breakout-v1", "trend-pullback-research-v1",
            "custom-dip-reclaim-v1",
        ])
        self.assertTrue(PROTOCOL["no_tri_or_price_nifty_substitute"])
        self.assertFalse(PROTOCOL["live_trading"])
        self.assertFalse(PROTOCOL["sample_is_independent_out_of_sample"])

    def test_matched_two_fee_scenarios_no_trading_or_promotion(self):
        calls=[]
        def simulate(d,start,end,**kwargs):
            calls.append(kwargs)
            return fake_sim(d,start,end,**kwargs)
        builders=tuple((name,fake_builder) for name,_ in STRATEGIES)
        result=compare(synthetic(),"synthetic_adjusted_digest",
                       limit=1,simulator=simulate,builders=builders)
        self.assertEqual(len(calls),6)
        self.assertTrue(all(x["risk_fraction"]==.01 for x in calls))
        self.assertTrue(all(x["max_hold"]==100 for x in calls))
        self.assertTrue(all(x["capital"]==CAPITAL for x in calls))
        self.assertEqual(sorted(x["slip"] for x in calls),[.001]*3+[.002]*3)
        self.assertEqual(sorted(x["stress_multiplier"] for x in calls),
                         [1.]*3+[2.]*3)
        self.assertEqual(len(result["summary"]),3)
        self.assertEqual(result["eligible_historical_stocks"],1)
        self.assertEqual(result["sampled_stocks"],1)
        self.assertEqual(len(result["per_stock_observations"]),3)
        for name in result["summary"]:
            score=result["summary"][name]
            self.assertEqual(score["completed_trades"],1)
            self.assertEqual(score["censored_sample_end_trades"],1)
            self.assertEqual(score["overnight_gap_below_stop_exits"],1)
            self.assertEqual(score["modeled_stop_risk_budget_breaches"],1)
            self.assertEqual(score["mean_completed_net_position_return"],-.02)
            self.assertEqual(score["mean_double_cost_net_position_return"],-.04)
        self.assertFalse(result["benchmark_based_strategy_tested"])
        self.assertFalse(result["tri_source_available_for_this_run"])
        self.assertFalse(result["historical_point_in_time_fundamentals_verified"])
        self.assertFalse(result["strategy_profitability_validated"])
        self.assertEqual(result["verified_profitable_strategies"],0)
        self.assertIsNone(result["calibrated_win_probability"])
        self.assertFalse(result["live_eligible"])
        self.assertEqual(result["orders"],"DISABLED")

    def test_sample_end_is_not_a_completed_winner(self):
        out=completed([
            {"Reason":"stop","Return":-.03},
            {"Reason":"sample_end","Return":10.},
        ])
        self.assertEqual(out["count"],1)
        self.assertEqual(out["wins"],0)
        self.assertEqual(out["sample_end_censored_trades"],1)
        self.assertEqual(out["mean_completed_net_position_return"],-.03)

    def test_future_prices_after_freeze_cannot_rewrite_research(self):
        builders=tuple((name,fake_builder) for name,_ in STRATEGIES)
        old=synthetic()
        new=old.iloc[-1:].copy()
        new["Date"]=pd.Timestamp("2026-11-11")
        new["Close"]=5000.
        new["Open"]=4999.
        new["High"]=5010.
        new["Low"]=4990.
        extended=pd.concat([old,new],ignore_index=True)
        a=compare(old,"synthetic",limit=1,simulator=fake_sim,builders=builders)
        b=compare(extended,"synthetic",limit=1,simulator=fake_sim,builders=builders)
        self.assertEqual(a["sample_symbols"],b["sample_symbols"])
        self.assertEqual(a["summary"],b["summary"])
        self.assertEqual(a["per_stock_observations"],b["per_stock_observations"])

    def test_short_source_history_has_no_research_eligible_stocks(self):
        result=compare(
            synthetic().tail(300),"synthetic_digest",limit=1,
            simulator=fake_sim,
            builders=tuple((name,fake_builder) for name,_ in STRATEGIES),
        )
        self.assertEqual(result["state"],"NO_STOCK_WITH_1260_ADJUSTED_BARS_BEFORE_CUTOFF")
        self.assertEqual(result["eligible_stocks"],0)
        self.assertFalse(result["live_eligible"])
        self.assertEqual(result["orders"],"DISABLED")

    def test_invalid_limit_and_missing_source_hash_fail_shut(self):
        b=synthetic()
        for bad in (0,61,True,"50"):
            with self.subTest(limit=bad), self.assertRaisesRegex(
                ValueError,"PRICE_ONLY_LIMIT_1_TO_60"
            ):
                compare(b,"hash",limit=bad)
        with self.assertRaisesRegex(ValueError,"SOURCE_HASH_ATTESTATION_REQUIRED"):
            compare(b,"",limit=1)

    def test_deterministic_sample_not_return_ranking(self):
        x=pd.concat([
            synthetic(symbol="ZZZ"),synthetic(symbol="AAA")
        ],ignore_index=True)
        builders=tuple((name,fake_builder) for name,_ in STRATEGIES)
        y=compare(x,"synthetic_hash",limit=1,
                  simulator=fake_sim,builders=builders)
        expected=sorted(["AAA","ZZZ"],
                        key=lambda z: hashlib.sha256(z.encode()).hexdigest())[0]
        self.assertEqual(y["sample_symbols"],[expected])


if __name__ == "__main__":
    unittest.main()
