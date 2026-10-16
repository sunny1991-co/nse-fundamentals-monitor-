"""Synthetic CI checks only; there is no real-market profitability claim."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research_monitor.exploratory_ranking import rank_exploratory
from swing_pullback_research import compare, PROTOCOL


def row(**kwargs):
    base = {
        "pooled_completed_trades": 45,
        "pooled_stress_completed_trades": 42,
        "stocks_with_completed_trades": 15,
        "pooled_completed_mean_net_return": 0.012,
        "pooled_stress_completed_mean_net_return": 0.006,
        "pooled_completed_profit_factor": 1.8,
        "worst_single_stock_drawdown": 0.14,
    }
    base.update(kwargs)
    return base


class ResearchRankingTests(unittest.TestCase):
    def test_tiny_positive_results_never_qualify(self):
        value = rank_exploratory({"pullback": row(
            pooled_completed_trades=11, pooled_stress_completed_trades=9,
            stocks_with_completed_trades=5)})
        self.assertIsNone(value["research_leader"])
        self.assertIn("INSUFFICIENT_COMPLETED_TRADES", value["observations"][0]["blockers"])
        self.assertFalse(value["live_eligible"])
        self.assertEqual(value["verified_profitable_strategies"], 0)

    def test_highest_double_cost_net_mean_only_ranks_exploratory(self):
        value = rank_exploratory({
            "breakout": row(pooled_stress_completed_mean_net_return=0.003),
            "pullback": row(pooled_stress_completed_mean_net_return=0.008),
        })
        self.assertEqual(value["research_leader"], "pullback")
        self.assertEqual(value["candidate_order"], ["pullback", "breakout"])
        self.assertEqual(value["status"], "EXPLORATORY_LEADER_REQUIRES_FRESH_VALIDATION")
        self.assertFalse(value["live_eligible"])
        self.assertEqual(value["trade_execution"], "DISABLED")
        self.assertEqual(value["verified_profitable_strategies"], 0)

    def test_missing_nonfinite_and_losing_stress_fail_closed(self):
        cases = [
            row(pooled_stress_completed_mean_net_return=-0.0001),
            row(pooled_completed_profit_factor=None),
            row(pooled_completed_profit_factor=float("inf")),
            row(worst_single_stock_drawdown=0.21),
            row(stocks_with_completed_trades=0),
            row(pooled_completed_mean_net_return=0),
        ]
        for observed in cases:
            with self.subTest(observed=observed):
                value = rank_exploratory({"strategy": observed})
                self.assertIsNone(value["research_leader"])
                self.assertFalse(value["observations"][0]["research_gate_pass"])

    def test_comparison_pools_completed_trades_not_forced_sample_end_winners(self):
        dates = pd.date_range("2020-01-01", periods=1300)
        close = np.linspace(100, 160, len(dates))
        data = pd.DataFrame({
            "Symbol": ["SYNTH"] * len(dates), "Date": dates,
            "Open": close, "High": close*1.02, "Low": close*.98,
            "Close": close, "Volume": np.full(len(dates), 2e6),
        })
        def pretend(_bars, _start, _end, **kwargs):
            losses = -0.06 if "cost" in kwargs else -0.03
            trades = [
                {"Reason": "stop", "Return": losses},
                {"Reason": "sample_end", "Return": 0.99},
            ]
            return {
                "Completed_Trades": 1, "Trades": 2,
                "Mean_Return": 0.48, "Max_Drawdown": 0.1,
            }, trades
        with patch("swing_pullback_research.simulate", side_effect=pretend):
            result = compare(data, "synthetic-not-NSE", 1)
        for strategy in ("breakout-v1", PROTOCOL["version"]):
            summary = result["summary"][strategy]
            self.assertEqual(summary["pooled_completed_trades"], 1)
            self.assertEqual(summary["pooled_stress_completed_trades"], 1)
            self.assertEqual(summary["stocks_with_completed_trades"], 1)
            self.assertAlmostEqual(summary["pooled_completed_mean_net_return"], -0.03)
            self.assertAlmostEqual(summary["pooled_stress_completed_mean_net_return"], -0.06)
        self.assertIsNone(result["research_selection"]["research_leader"])
        self.assertFalse(result["live_eligible"])


if __name__ == "__main__":
    unittest.main()
