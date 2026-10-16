"""Offline forensic checks: stop-first assumptions and net-P&L reconciliation.

These are synthetic regression tests; they do not establish profitability.
"""
import unittest
import numpy as np
import pandas as pd

from research_monitor.groww_delivery_costs import simulate_delivery
from swing_loss_diagnostic import _describe


def frame(count=224):
    close = np.full(count, 1000.0)
    dates = pd.date_range("2023-01-01", periods=count)
    bars = pd.DataFrame({
        "Date": dates,
        "Open": close.copy(),
        "High": close + 5,
        "Low": close - 5,
        "Close": close.copy(),
        "Volume": np.full(count, 500000.),
        "ATR": np.full(count, 10.),
        "Stop": np.full(count, 980.),
        "Signal": np.zeros(count, dtype=bool),
    })
    bars.loc[219, "Signal"] = True
    return bars


class LossForensicsTests(unittest.TestCase):
    def test_stop_gap_with_both_stop_target_touch_keeps_stop_first(self):
        d=frame()
        # 220 = next-open entry. 221 gaps below 980 stop but touches target.
        d.loc[221, ["Open","Low","High","Close"]]=[960.,940.,1100.,980.]
        stats, trades=simulate_delivery(d,220,len(d))
        self.assertEqual(stats["Completed_Trades"],1)
        self.assertEqual(len(trades),1)
        t=trades[0]
        self.assertEqual(t["Reason"],"stop")
        self.assertTrue(t["Gap_Below_Stop"])
        self.assertTrue(t["Stop_And_Target_Both_Touched"])
        self.assertFalse(t["Trailing_Stop_Active"])
        self.assertFalse(t["Partial_Profit_Taken"])
        self.assertFalse(t["Exit_On_Entry_Session"])
        self.assertEqual(t["Initial_Stop_Rs"],980.)
        self.assertEqual(t["Stop_At_Exit_Rs"],980.)
        self.assertEqual(t["Exit_Open_Rs"],960.)
        self.assertLess(t["Return"], 0)
        self.assertAlmostEqual(
            t["Net_Profit_Rs"],
            t["Sell_Turnover_Rs"] - t["Buy_Turnover_Rs"]
            - t["All_Trading_Charges_Rs"],
        )
        desc=_describe(trades)
        flags=desc["forensics"]
        self.assertEqual(flags["Gap_Below_Stop"],1)
        self.assertEqual(flags["Stop_And_Target_Both_Touched"],1)
        self.assertEqual(flags["Stop_At_Initial_Level_Or_Lower"],1)

    def test_partial_profit_then_trailing_stop_is_labeled(self):
        d=frame(225)
        # Stop 980, buy about 1001, first target >1040.
        d.loc[221, ["Open","Low","High","Close"]]=[
            1030.,1020.,1055.,1050.
        ]
        d.loc[222, ["Open","Low","High","Close"]]=[
            1035.,1025.,1055.,1033.
        ]
        # Row 221 touches T1 and not stop, moves stop to entry
        # via partial exit / trailing adjustment. Day 222 hits new stop.
        stats, trades=simulate_delivery(d,220,223)
        self.assertEqual(stats["Completed_Trades"],1)
        self.assertEqual(len(trades),1)
        t=trades[0]
        self.assertEqual(t["Reason"],"stop")
        self.assertTrue(t["Partial_Profit_Taken"])
        self.assertTrue(t["Partial_Previously_Taken"])
        self.assertTrue(t["Trailing_Stop_Active"])
        self.assertFalse(t["Gap_Below_Stop"])
        self.assertGreater(t["Stop_At_Exit_Rs"],t["Initial_Stop_Rs"])

    def test_censored_trades_do_not_count_as_completed_wins(self):
        d=frame()
        stats, trades=simulate_delivery(d,220,223)
        self.assertEqual(len(trades),1)
        self.assertEqual(trades[0]["Reason"],"sample_end")
        self.assertEqual(stats["Completed_Trades"],0)
        result=_describe(trades)
        self.assertEqual(result["completed_trades"],0)
        self.assertIsNone(result["mean_net_trade_return"])


if __name__=="__main__":
    unittest.main()
