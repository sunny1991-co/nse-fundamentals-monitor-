"""GitHub-only strategy safeguards. Synthetic fixtures are NOT market validation."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from swing_backtest import COST, SLIP, VERSION
from swing_pullback_research import PROTOCOL, compare


def synthetic_history():
    """Fixed, fabricated OHLCV, never represented as licensed NSE prices."""
    rng = np.random.default_rng(20261016)
    close = 120.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.012, 1330)))
    return pd.DataFrame({
        "Symbol": ["SYNTHETIC"] * len(close),
        "Date": pd.date_range("2020-01-01", periods=len(close)),
        "Open": close.copy(),
        "High": close * 1.02,
        "Low": close * 0.98,
        "Close": close,
        "Volume": np.full(len(close), 1_500_000.0),
    })


class GitHubPullbackEvidenceGuards(unittest.TestCase):
    def test_apparent_high_profit_never_certifies_strategy(self):
        # Even an intentionally implausible mocked win cannot pass the live gate.
        fictional = {"Completed_Trades": 40, "Trades": 40,
                     "Mean_Return": 0.25, "Max_Drawdown": 0.0}
        with patch("swing_pullback_research.simulate", return_value=(fictional, [])):
            result = compare(synthetic_history(), "synthetic-not-a-price-source", 1)
        self.assertEqual(result["validation_status"], "NOT_VALIDATED")
        self.assertFalse(result["live_eligible"])
        self.assertEqual(result["scope"], "EXPLORATORY_COMPARISON_NOT_STRATEGY_VALIDATION")
        self.assertNotIn("verified_probability", result)
        self.assertEqual(result["summary"][PROTOCOL["version"]]["stocks_with_completed_trades_and_positive_mean"], 1)

    def test_both_strategies_use_double_cost_and_double_slippage_stress(self):
        fictional = {"Completed_Trades": 1, "Trades": 1,
                     "Mean_Return": 0.01, "Max_Drawdown": 0.02}
        with patch("swing_pullback_research.simulate", return_value=(fictional, [])) as sim:
            result = compare(synthetic_history(), "synthetic-only", 1)
        self.assertEqual(set(result["summary"]), {VERSION, PROTOCOL["version"]})
        self.assertEqual(sim.call_count, 4)
        for call in sim.call_args_list:
            if "cost" in call.kwargs:
                self.assertAlmostEqual(call.kwargs["cost"], 2 * COST)
                self.assertAlmostEqual(call.kwargs["slip"], 2 * SLIP)
            else:
                self.assertNotIn("slip", call.kwargs)
        self.assertEqual(sum("cost" in call.kwargs for call in sim.call_args_list), 2)

    def test_appending_post_design_bars_cannot_change_research_results(self):
        old = synthetic_history()
        # Keep the original 1,330 sessions and add a large post-design rally.
        future_dates = pd.date_range("2026-10-16", periods=90)
        fabricated = pd.DataFrame({
            "Symbol": ["SYNTHETIC"] * len(future_dates),
            "Date": future_dates,
            "Open": np.linspace(500, 1100, len(future_dates)),
            "High": np.linspace(510, 1110, len(future_dates)),
            "Low": np.linspace(490, 1090, len(future_dates)),
            "Close": np.linspace(500, 1100, len(future_dates)),
            "Volume": [3_000_000.0] * len(future_dates),
        })
        base = compare(old, "synthetic-only", 1)
        changed = compare(pd.concat([old, fabricated], ignore_index=True), "synthetic-only", 1)
        self.assertEqual(base["summary"], changed["summary"])
        self.assertEqual(base["stock_observations"], changed["stock_observations"])
        self.assertFalse(changed["live_eligible"])


if __name__ == "__main__":
    unittest.main()
