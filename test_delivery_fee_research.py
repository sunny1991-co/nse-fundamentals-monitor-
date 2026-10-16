"""Fee-aware strategy comparison: synthetic tests, never actual market P&L."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from swing_delivery_fee_research import (
    BUILDERS, VERSION_ID, compare, _signals_for,
)
from swing_backtest import features


def fake_stock(symbol="TEST", count=1320):
    rng = np.random.default_rng(12)
    prices = 100 * np.exp(np.cumsum(rng.normal(.001, .012, count)))
    return pd.DataFrame({
        "Symbol": [symbol]*count,
        "Date": pd.date_range("2020-01-01", periods=count),
        "Open": prices, "High": prices*1.017,
        "Low": prices*.983, "Close": prices,
        "Volume": np.full(count, 1000000.),
    })


class FeeResearchTests(unittest.TestCase):
    def test_seven_distinct_price_only_rules(self):
        self.assertEqual(len(BUILDERS), 7)
        self.assertEqual(len({name for name, _ in BUILDERS}), 7)
        self.assertEqual(VERSION_ID, "groww-delivery-cost-research-v1")

    def test_respects_price_bar_causality(self):
        raw = fake_stock()
        for name, builder in BUILDERS:
            with self.subTest(strategy=name):
                partial = _signals_for(name, builder, raw.iloc[:800])
                full = _signals_for(name, builder, raw)
                pd.testing.assert_series_equal(
                    partial.Signal, full.Signal.iloc[:800].reset_index(drop=True)
                )

    def test_uses_fee_engine_and_double_cost_scenario(self):
        fake_stats = {
            "Trades": 2, "Completed_Trades": 1,
            "Max_Drawdown": .05,
            "Estimated_All_Trading_Charges_Rs": 200,
        }
        trades = [
            {"Reason": "stop", "Return": -0.03},
            {"Reason": "sample_end", "Return": 1.00},
        ]
        with patch("swing_delivery_fee_research.simulate_delivery",
                   return_value=(fake_stats, trades)) as sim:
            report = compare(fake_stock(), "synthetic-not-NSE", 1)
        self.assertEqual(sim.call_count, 14)
        for i, call in enumerate(sim.call_args_list):
            self.assertEqual(call.kwargs["capital"], 100000)
            self.assertEqual(call.kwargs["stress_multiplier"], 1 if i%2 == 0 else 2)
            self.assertEqual(call.kwargs["slip"], .001 if i%2 == 0 else .002)
        self.assertEqual(len(report["summary"]), 7)
        for item in report["summary"].values():
            self.assertEqual(item["pooled_completed_trades"], 1)
            self.assertEqual(item["pooled_stress_completed_trades"], 1)
            self.assertEqual(item["pooled_completed_mean_net_return"], -.03)
            self.assertEqual(item["pooled_stress_completed_mean_net_return"], -.03)
            self.assertEqual(item["sum_of_independent_stock_test_charges_rs"], 200)
        self.assertIsNone(report["research_selection"]["research_leader"])
        self.assertEqual(report["research_top_three_unvalidated"], [])
        self.assertEqual(report["verified_top_three"], [])
        self.assertEqual(report["verified_profitable_strategies"], 0)
        self.assertEqual(report["orders"], "DISABLED")
        self.assertFalse(report["live_eligible"])

    def test_no_strategy_gets_future_bars(self):
        stock = fake_stock()
        future = stock.iloc[-1:].copy()
        future["Date"] = pd.Timestamp("2026-10-16")
        future["Close"] = 1e8
        future["Open"] = 1e8
        future["High"] = 1e8
        future["Low"] = 1e8
        empty = {
            "Trades": 0, "Completed_Trades": 0,
            "Max_Drawdown": 0.,
            "Estimated_All_Trading_Charges_Rs": 0.,
        }
        with patch("swing_delivery_fee_research.simulate_delivery",
                   return_value=(empty, [])):
            before = compare(stock, "fake", 1)
            after = compare(pd.concat([stock, future]), "fake", 1)
        self.assertEqual(before["summary"], after["summary"])
        self.assertEqual(before["stock_observations"], after["stock_observations"])

    def test_unavailable_history_and_invalid_limits_do_not_prove_profit(self):
        with self.assertRaises(ValueError):
            compare(fake_stock(), "fake", 101)
        with self.assertRaises(ValueError):
            compare(fake_stock(), "fake", 0)
        future = fake_stock()
        future["Date"] = pd.date_range("2026-10-16", periods=len(future))
        with patch("swing_delivery_fee_research.simulate_delivery") as sim:
            report = compare(future, "fake", 1)
        sim.assert_not_called()
        self.assertEqual(report["eligible_universe"], 0)
        self.assertIsNone(report["research_selection"]["research_leader"])
        self.assertFalse(report["live_eligible"])


if __name__ == "__main__":
    unittest.main()
