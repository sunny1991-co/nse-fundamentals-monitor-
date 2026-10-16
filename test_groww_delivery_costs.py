"""Offline regression tests for *estimated* Groww delivery charges.

No real brokerage account, filled orders, or profitable trading assumed.
"""
import unittest

import numpy as np
import pandas as pd

from research_monitor.groww_delivery_costs import (
    groww_delivery_breakdown, simulate_delivery,
)


def fake_bars():
    n = 225
    dates = pd.date_range("2024-01-01", periods=n)
    df = pd.DataFrame({
        "Date": dates,
        "Open": np.full(n, 1000.0),
        "High": np.full(n, 1004.0),
        "Low": np.full(n, 996.0),
        "Close": np.full(n, 1000.0),
        "Volume": np.full(n, 200_000.0),
        "Signal": [False]*n,
        "Stop": np.full(n, 980.0),
        "ATR": np.full(n, 10.0),
    })
    df.loc[219, "Signal"] = True
    df.loc[220, ["Low", "High", "Close"]] = [995., 1007., 1002.]
    df.loc[221, ["Open", "Low", "High", "Close"]] = [968., 958., 1040., 980.]
    return df


class GrowwCostsTests(unittest.TestCase):
    def test_pricing_components_and_gst(self):
        b = groww_delivery_breakdown(100000, "buy")
        s = groww_delivery_breakdown(100000, "sell")
        self.assertAlmostEqual(b.brokerage, 20.)
        self.assertAlmostEqual(b.stt, 100.)
        self.assertAlmostEqual(b.stamp, 15.)
        self.assertAlmostEqual(b.exchange, 2.97)
        self.assertAlmostEqual(b.sebi, .1)
        self.assertAlmostEqual(b.ipft, .1)
        self.assertAlmostEqual(b.dp, 0.)
        self.assertAlmostEqual(s.stamp, 0.)
        self.assertAlmostEqual(s.dp, 20.)
        self.assertAlmostEqual(b.gst, .18*(20+2.97+.1+.1))
        self.assertGreater(s.total, b.total-15)
        self.assertGreater(s.total, 0)

    def test_minimum_brokerage_and_low_value_dp_waiver(self):
        b = groww_delivery_breakdown(1000, "buy")
        tiny = groww_delivery_breakdown(100, "buy")
        self.assertAlmostEqual(b.brokerage, 5)
        self.assertAlmostEqual(tiny.brokerage, 2.5)
        self.assertAlmostEqual(
            groww_delivery_breakdown(99, "sell").dp, 0.,
        )
        self.assertAlmostEqual(
            groww_delivery_breakdown(100, "sell").dp, 20.,
        )

    def test_more_costs_never_improve_fees(self):
        for turnover in (99, 100, 200, 1000, 50000, 1000000):
            for side in ("buy", "sell"):
                a = groww_delivery_breakdown(turnover, side)
                b = groww_delivery_breakdown(
                    turnover, side, stress_multiplier=2
                )
                self.assertAlmostEqual(b.total, 2*a.total)

    def test_invalid_inputs_fail_closed(self):
        for money in (-100, 0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                groww_delivery_breakdown(money, "buy")
        with self.assertRaises(ValueError):
            groww_delivery_breakdown(1000, "hold")
        with self.assertRaises(ValueError):
            groww_delivery_breakdown(1000, "sell", stress_multiplier=.5)

    def test_next_open_and_stop_first_with_net_rupee_costs(self):
        d = fake_bars()
        normal, trades = simulate_delivery(d, 220, len(d))
        stressed, stress_trades = simulate_delivery(
            d, 220, len(d), slip=.002, stress_multiplier=2,
        )
        self.assertEqual(normal["Completed_Trades"], 1)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["Reason"], "stop")
        self.assertEqual(trades[0]["Entry_Date"], str(d.Date.iloc[220].date()))
        self.assertEqual(trades[0]["Exit_Date"], str(d.Date.iloc[221].date()))
        self.assertGreater(trades[0]["All_Trading_Charges_Rs"], 0)
        self.assertLess(trades[0]["Net_Profit_Rs"], 0)
        self.assertLess(stress_trades[0]["Net_Profit_Rs"], trades[0]["Net_Profit_Rs"])
        self.assertGreater(
            stressed["Estimated_All_Trading_Charges_Rs"],
            normal["Estimated_All_Trading_Charges_Rs"],
        )
        self.assertFalse(normal["Open_Position"])

    def test_forced_sample_end_not_counted_completed(self):
        d = fake_bars()
        d.loc[221:, ["Open", "Low", "High", "Close"]] = [1000., 995., 1005., 1000.]
        result, trades = simulate_delivery(d, 220, len(d))
        self.assertEqual(result["Trades"], 1)
        self.assertEqual(result["Completed_Trades"], 0)
        self.assertEqual(trades[0]["Reason"], "sample_end")
        self.assertIsNone(result["Completed_Mean_Net_Return"])

    def test_missing_signal_never_trades(self):
        d = fake_bars()
        d["Signal"] = False
        result, trades = simulate_delivery(d, 220, len(d))
        self.assertEqual(len(trades), 0)
        self.assertEqual(result["Completed_Trades"], 0)


if __name__ == "__main__":
    unittest.main()
