"""Synthetic loss attribution: no parameter optimization, no trade orders."""
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

from swing_loss_diagnostic import _forensic_groups, diagnose


def completed(symbol, reason, ret, *, trailing=False, gap=False, both=False):
    return {
        "symbol": symbol, "Reason": reason, "Return": ret,
        "Buy_Turnover_Rs": 10000.0,
        "Sell_Turnover_Rs": 10000.0 * (1+ret) + 50.0,
        "All_Trading_Charges_Rs": 50.0,
        "Held_Sessions": 5,
        "Entry_Date": "2025-01-01", "Exit_Date": "2025-01-06",
        "Trailing_Stop_Active": trailing,
        "Gap_Below_Stop": gap,
        "Stop_And_Target_Both_Touched": both,
    }


def history(symbol, extreme=False):
    count=1300
    price=np.linspace(100,200,count)
    if extreme:
        price[900]=price[899]*1.5
    return pd.DataFrame({
        "Symbol": [symbol]*count,
        "Date": pd.date_range("2020-01-01", periods=count),
        "Open": price, "High": price*1.01,
        "Low": price*.99, "Close": price,
        "Volume": np.full(count,1_000_000.0),
    })


class LossAttributionTests(unittest.TestCase):
    def test_partition_reconciles_completed_trades_and_excludes_end(self):
        trades = [
            completed("A", "stop", -.09, gap=True, both=True),
            completed("B", "stop", -.02, trailing=True),
            completed("B", "target2", .08),
            completed("A", "sample_end", .90),
        ]
        groups=_forensic_groups(trades,{"A"},["A","B","C"])
        self.assertEqual(groups["all_completed"]["completed_trades"],3)
        self.assertEqual(groups["stop_exits"]["completed_trades"],2)
        self.assertEqual(groups["initial_stop_exits"]["completed_trades"],1)
        self.assertEqual(groups["trailing_stop_exits"]["completed_trades"],1)
        self.assertEqual(groups["gap_below_stop_exits"]["completed_trades"],1)
        self.assertEqual(groups["non_gap_stop_exits"]["completed_trades"],1)
        self.assertEqual(groups["stop_and_target_same_bar"]["completed_trades"],1)
        self.assertEqual(groups["target2_exits"]["completed_trades"],1)
        flagged=groups["extreme_jump_flagged_symbols"]
        other=groups["not_extreme_jump_flagged_symbols"]
        self.assertEqual(flagged["completed_trades"],1)
        self.assertEqual(other["completed_trades"],2)
        self.assertEqual(
            flagged["completed_trades"]+other["completed_trades"],
            groups["all_completed"]["completed_trades"],
        )
        self.assertEqual(groups["extreme_jump_flagged_symbol_count_in_sample"],1)
        self.assertEqual(groups["not_extreme_jump_flagged_symbol_count_in_sample"],2)
        self.assertAlmostEqual(groups["all_completed"]["mean_net_return_pct"],-1)
        self.assertGreater(groups["all_completed"]["mean_fee_drag_percentage_points"],0)

    def test_legacy_rows_with_no_stop_flags_are_unknown(self):
        trades=[completed("A","stop",-.05)]
        del trades[0]["Gap_Below_Stop"]
        del trades[0]["Trailing_Stop_Active"]
        groups=_forensic_groups(trades,set(),["A"])
        self.assertIsNone(groups["gap_below_stop_exits"])
        self.assertIsNone(groups["initial_stop_exits"])
        self.assertEqual(groups["stop_exits"]["completed_trades"],1)

    def test_empty_groups_are_null_not_proven_winners(self):
        groups=_forensic_groups([],set(),["NO_TRADES"])
        self.assertEqual(groups["all_completed"]["completed_trades"],0)
        self.assertIsNone(groups["all_completed"]["mean_net_return_pct"])
        self.assertIsNone(groups["initial_stop_exits"]["mean_net_return_pct"])

    def test_real_diagnostic_routes_jump_flags_to_trade_sensitivity(self):
        stock_a, stock_b=history("A",extreme=True), history("B")
        ledger=[
            completed("A","stop",-.07,gap=True),
            completed("A","sample_end",.90),
        ]
        def sim(d, *args, **kwargs):
            # Trade symbol is injected by diagnose itself.
            symbol=str(d.Date.iloc[-1].date())  # do not infer symbol from d
            return ({}, [dict(t) for t in ledger]
                    if d.Close.max()>220 else
                    [completed("B","stop",-.02)])
        with patch("swing_loss_diagnostic.simulate_delivery",side_effect=sim):
            result=diagnose(pd.concat([stock_a,stock_b]),"SYNTHETIC_ONLY",
                            limit=2,selected=["vcp-breakout-v1"])
        self.assertFalse(result["live_eligible"])
        self.assertEqual(result["orders"],"DISABLED")
        self.assertEqual(result["analysed_stocks"],2)
        metrics=result["summary"]["vcp-breakout-v1"]["loss_attribution"]
        self.assertEqual(metrics["extreme_jump_flagged_symbol_count_in_sample"],1)
        self.assertEqual(metrics["not_extreme_jump_flagged_symbol_count_in_sample"],1)
        self.assertGreater(metrics["extreme_jump_flagged_symbols"]["completed_trades"],0)
        self.assertGreater(metrics["not_extreme_jump_flagged_symbols"]["completed_trades"],0)


if __name__=="__main__":
    unittest.main()
