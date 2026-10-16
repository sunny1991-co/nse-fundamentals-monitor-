"""CI evidence guards use synthetic fixtures, never claim NSE profitability."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from swing_pullback_research import completed_trade_stats, compare, PROTOCOL


class CompletedTradeReportingTests(unittest.TestCase):
    def test_no_completed_trades_never_claims_zero_or_positive_mean(self):
        self.assertEqual(
            completed_trade_stats([{"Reason": "sample_end", "Return": 0.60}]),
            {"count": 0, "mean_net_return": None, "profit_factor": None},
        )
        self.assertEqual(
            completed_trade_stats([]),
            {"count": 0, "mean_net_return": None, "profit_factor": None},
        )

    def test_forced_liquidation_win_cannot_hide_completed_loss(self):
        result = completed_trade_stats([
            {"Reason": "stop", "Return": -0.03},
            {"Reason": "target2", "Return": 0.01},
            {"Reason": "sample_end", "Return": 0.90},
        ])
        self.assertEqual(result["count"], 2)
        self.assertAlmostEqual(result["mean_net_return"], -0.01)
        self.assertAlmostEqual(result["profit_factor"], 1/3)

    def test_no_loss_profit_factor_is_not_reported_as_infinite(self):
        result = completed_trade_stats([
            {"Reason": "target2", "Return": 0.04},
            {"Reason": "time", "Return": 0.01},
        ])
        self.assertEqual(result["count"], 2)
        self.assertAlmostEqual(result["mean_net_return"], 0.025)
        self.assertIsNone(result["profit_factor"])

    def test_research_summary_uses_completed_only_metrics_separately(self):
        dates = pd.date_range("2020-01-01", periods=1280)
        close = np.linspace(100, 300, len(dates))
        frame = pd.DataFrame({
            "Symbol": ["SYNTH"] * len(dates),
            "Date": dates,
            "Open": close,
            "High": close * 1.03,
            "Low": close * 0.97,
            "Close": close,
            "Volume": [2_000_000.] * len(dates),
        })

        def invented_sim(_data, _start, _end, **kwargs):
            stress = "cost" in kwargs
            trades = [
                {"Reason": "stop", "Return": -0.05 if stress else -0.02},
                {"Reason": "sample_end", "Return": 0.75 if stress else 0.95},
            ]
            stats = dict(Completed_Trades=1, Trades=2, Mean_Return=0.45,
                         Max_Drawdown=0.1)
            return stats, trades

        with patch("swing_pullback_research.simulate", side_effect=invented_sim):
            result = compare(frame, "synthetic-digest-not-source-evidence", 1)
        for version in ("breakout-v1", PROTOCOL["version"]):
            row = next(r for r in result["stock_observations"]
                       if r["strategy"] == version)
            self.assertGreater(row["mean_trade_return_including_sample_end"], 0)
            self.assertAlmostEqual(row["completed_mean_net_trade_return"], -0.02)
            self.assertAlmostEqual(row["stress_completed_mean_net_trade_return"], -0.05)
            self.assertEqual(result["summary"][version]["stocks_with_positive_completed_mean"], 0)
            self.assertEqual(result["summary"][version]["stocks_with_positive_completed_mean_under_stress"], 0)
            self.assertEqual(result["summary"][version]["stocks_with_at_least_30_completed_trades"], 0)
        self.assertEqual(result["validation_status"], "NOT_VALIDATED")
        self.assertFalse(result["live_eligible"])


if __name__ == "__main__":
    unittest.main()
