"""Synthetic checks for original custom swing hypothesis; not NSE P&L evidence."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from swing_backtest import COST, SLIP
from swing_custom_strategy_research import (
    custom_features, compare, NAME, END_DATE,
)


def fixture(symbol="SYNTH", count=1350):
    rng = np.random.default_rng(20261016)
    close = 100 * np.exp(np.cumsum(rng.normal(.0008, .012, count)))
    return pd.DataFrame({
        "Symbol": [symbol] * count,
        "Date": pd.date_range("2020-01-01", periods=count),
        "Open": close, "High": close*1.016, "Low": close*.984,
        "Close": close, "Volume": np.full(count, 3_000_000.0),
    })


class CustomStrategyTests(unittest.TestCase):
    def test_signal_causality_and_input_not_modified(self):
        original = fixture()
        saved = original.copy(deep=True)
        first = custom_features(original.iloc[:850])
        full = custom_features(original)
        pd.testing.assert_frame_equal(saved, original)
        pd.testing.assert_frame_equal(
            first, full.iloc[:850].reset_index(drop=True)
        )
        self.assertEqual(full.Signal.dtype, bool)

    def test_realistic_constructed_dip_and_reclaim_can_signal(self):
        # Construct a strong uptrend, controlled dip, then close near session high.
        n = 500
        close = np.array([100 + .2*i for i in range(n)], dtype=float)
        close[-3:] = [193, 194, 205]
        raw = pd.DataFrame({
            "Symbol": ["SYNTH"]*n,
            "Date": pd.date_range("2020-01-01", periods=n),
            "Open": close, "High": close + 1, "Low": close - 1,
            "Close": close, "Volume": [2_000_000.]*n,
        })
        raw.loc[n-1, "High"] = 205.5
        raw.loc[n-1, "Low"] = 202
        # RSI is held neutral to isolate the precise event geometry.
        def controlled_features(x):
            return x.reset_index(drop=True).assign(
                ATR=2., Stop=x.reset_index(drop=True).Close-4,
                RSI=60., Signal=False
            )
        with patch("swing_custom_strategy_research.features",
                   side_effect=controlled_features):
            observed = custom_features(raw)
        self.assertTrue(bool(observed.Signal.iloc[-1]))
        self.assertFalse(bool(observed.Signal.iloc[-2]))

    def test_stress_costs_and_forced_liquidations_never_prove_profit(self):
        report = {
            "Completed_Trades": 1, "Trades": 2,
            "Mean_Return": .43, "Max_Drawdown": .11,
        }
        trades = [
            {"Reason": "stop", "Return": -0.04},
            {"Reason": "sample_end", "Return": +0.90},
        ]
        with patch("swing_custom_strategy_research.simulate",
                   return_value=(report, trades)) as simulation:
            result = compare(fixture(), "synthetic-digest", 1)
        self.assertEqual(simulation.call_count, 6)
        self.assertEqual(sum("cost" in c.kwargs
                             for c in simulation.call_args_list), 3)
        for call in simulation.call_args_list:
            if "cost" in call.kwargs:
                self.assertAlmostEqual(call.kwargs["cost"], COST*2)
                self.assertAlmostEqual(call.kwargs["slip"], SLIP*2)
        for strategy in result["summary"]:
            self.assertEqual(
                result["summary"][strategy]["pooled_completed_trades"], 1
            )
            self.assertAlmostEqual(
                result["summary"][strategy]["pooled_completed_mean_net_return"],
                -.04
            )
        self.assertIsNone(result["research_selection"]["research_leader"])
        self.assertEqual(result["verified_profitable_strategies"], 0)
        self.assertEqual(result["validation_status"], "NOT_VALIDATED")
        self.assertFalse(result["live_eligible"])
        self.assertEqual(result["orders"], "DISABLED")

    def test_does_not_inspect_new_days_after_design(self):
        old = fixture()
        extra = old.iloc[-1:].copy()
        extra["Date"] = pd.Timestamp(END_DATE) + pd.Timedelta(days=1)
        extra["Close"] = 1_000_000
        extra["High"] = 1_000_001
        extra["Low"] = 999_999
        extra["Open"] = 1_000_000
        fake = {"Completed_Trades": 0, "Trades": 0,
                "Mean_Return": 0., "Max_Drawdown": 0.}
        with patch("swing_custom_strategy_research.simulate",
                   return_value=(fake, [])):
            first = compare(old, "synthetic", 1)
            later = compare(pd.concat([old, extra]), "synthetic", 1)
        self.assertEqual(first["summary"], later["summary"])
        self.assertEqual(first["stock_observations"],
                         later["stock_observations"])

    def test_predefined_symbol_order_and_limit(self):
        a, b = fixture("AAA"), fixture("BBB")
        fake = {"Completed_Trades": 0, "Trades": 0,
                "Mean_Return": 0., "Max_Drawdown": 0.}
        with patch("swing_custom_strategy_research.simulate",
                   return_value=(fake, [])):
            ab = compare(pd.concat([a, b]), "synthetic", 2)
            ba = compare(pd.concat([b, a]), "synthetic", 2)
        self.assertEqual(ab["sample_symbols"], ba["sample_symbols"])
        with self.assertRaises(ValueError):
            compare(a, "synthetic", 101)
        with self.assertRaises(ValueError):
            compare(a, "synthetic", 0)


if __name__ == "__main__":
    unittest.main()
