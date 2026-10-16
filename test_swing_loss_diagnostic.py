"""Offline synthetic diagnostic guards, not real NSE performance."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from swing_loss_diagnostic import _describe, diagnose


def bar_data(symbol="EXAMPLE", n=1300):
    dates=pd.date_range("2020-01-01", periods=n)
    prices=np.linspace(100, 200, n)
    return pd.DataFrame({
        "Symbol": [symbol]*n,
        "Date": dates, "Open": prices, "High": prices*1.01,
        "Low": prices*.99, "Close": prices,
        "Volume": np.full(n, 1_000_000.0),
    })


def trade(reason="stop", ret=-.04, buy=10000, sell=9600, fees=50):
    return {
        "Reason": reason, "Return": ret,
        "Buy_Turnover_Rs": buy, "Sell_Turnover_Rs": sell,
        "All_Trading_Charges_Rs": fees,
        "Held_Sessions": 5, "Entry_Date": "2025-01-01",
        "Exit_Date": "2025-01-06",
    }


class LossDiagnosticTests(unittest.TestCase):
    def test_exit_reasons_and_forced_end_not_profit_evidence(self):
        ledger=[
            trade("stop",-.06,buy=10000,sell=9450,fees=50),
            trade("target2",.08,buy=10000,sell=10860,fees=60),
            trade("sample_end",.80,buy=10000,sell=18050,fees=50),
        ]
        desc=_describe(ledger)
        self.assertEqual(desc["completed_trades"],2)
        self.assertEqual(desc["wins"],1)
        self.assertEqual(desc["losses"],1)
        self.assertAlmostEqual(desc["mean_net_trade_return"],.01)
        self.assertEqual(desc["by_exit_reason"]["stop"]["count"],1)
        self.assertEqual(desc["by_exit_reason"]["target2"]["count"],1)
        self.assertGreater(desc["mean_fee_drag_percentage_points"],0)

    def test_no_completed_trades_reports_null_not_zero_return(self):
        r=_describe([trade("sample_end",.8)])
        self.assertEqual(r["completed_trades"],0)
        self.assertIsNone(r["mean_net_trade_return"])

    def test_bounded_deterministic_and_no_live_promotion(self):
        fake=[
            trade("stop",-.04),
            trade("sample_end",.90,buy=10000,sell=19000,fees=50)
        ]
        with patch("swing_loss_diagnostic.simulate_delivery",
                   return_value=({},fake)) as sim:
            raw=pd.concat([bar_data("BBB"),bar_data("AAA")])
            result=diagnose(raw,"synthetic",limit=2,top=1,
                            selected=["custom-dip-reclaim-v1"])
        self.assertEqual(sim.call_count,2)
        self.assertEqual(result["analysed_stocks"],2)
        self.assertEqual(
            result["summary"]["custom-dip-reclaim-v1"]["completed_trades"],2
        )
        self.assertEqual(
            result["summary"]["custom-dip-reclaim-v1"]
            ["sample_end_liquidations_not_in_completed_metrics"],2
        )
        self.assertEqual(
            len(result["summary"]["custom-dip-reclaim-v1"]["worst_completed_trades"]),
            1,
        )
        self.assertFalse(result["live_eligible"])
        self.assertEqual(result["orders"],"DISABLED")

    def test_histories_after_design_do_not_enter_evaluation(self):
        raw=bar_data()
        extra=raw.iloc[-1:].copy()
        extra["Date"]=pd.Timestamp("2026-10-16")
        extra["Open"]=100000000
        extra["Close"]=100000000
        extra["High"]=100000001
        extra["Low"]=99999999
        with patch("swing_loss_diagnostic.simulate_delivery",
                   return_value=({},[trade("stop",-.04)])):
            a=diagnose(raw,"synthetic",limit=1,selected=["vcp-breakout-v1"])
            b=diagnose(pd.concat([raw,extra]),"synthetic",limit=1,
                       selected=["vcp-breakout-v1"])
        self.assertEqual(a["summary"],b["summary"])
        self.assertEqual(
            a["potential_extreme_jump_symbols_requiring_independent_review"],
            b["potential_extreme_jump_symbols_requiring_independent_review"],
        )

    def test_invalid_selection_and_limit_do_not_run(self):
        raw=bar_data()
        for value in (0,101):
            with self.assertRaises(ValueError):
                diagnose(raw,"fake",limit=value)
        with self.assertRaises(ValueError):
            diagnose(raw,"fake",selected=["made-up"])
        with self.assertRaises(ValueError):
            diagnose(raw,"fake",selected=["vcp-breakout-v1","vcp-breakout-v1"])

    def test_large_price_gaps_are_review_flags_not_auto_exclusions(self):
        raw=bar_data()
        raw.loc[800,["Open","High","Low","Close"]]=[310,313,308,310]
        # Keep the following day plausible for a separate reverse jump.
        with patch("swing_loss_diagnostic.simulate_delivery",
                   return_value=({},[])):
            result=diagnose(raw,"fake",limit=1,selected=["vcp-breakout-v1"])
        self.assertTrue(
            result["potential_extreme_jump_symbols_requiring_independent_review"]
        )
        self.assertFalse(result["live_eligible"])


if __name__=="__main__":
    unittest.main()
