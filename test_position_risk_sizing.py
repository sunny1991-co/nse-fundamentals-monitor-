"""Risk sizing and exposure reports tested on synthetic data, not NSE fills."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research_monitor.groww_delivery_costs import simulate_delivery
from swing_risk_sizing_research import compare, SCENARIOS, RISK_FRACTION


def bars(count=225):
    price=np.full(count,1000.)
    d=pd.DataFrame({
        "Date":pd.date_range("2024-01-01",periods=count),
        "Open":price.copy(),"High":price+5,"Low":price-5,
        "Close":price.copy(),"Volume":np.full(count,300000.),
        "Signal":np.zeros(count,dtype=bool),
        "Stop":np.full(count,980.),"ATR":np.full(count,10.),
    })
    d.loc[219,"Signal"]=True
    return d


def long_stock(count=1320):
    d=bars(count)
    d["Symbol"]="SYNTHETIC"
    d["Date"]=pd.date_range("2020-01-01",periods=count)
    return d


class PositionRiskTests(unittest.TestCase):
    def test_no_gap_initial_stop_includes_both_side_fees_under_budget(self):
        d=bars()
        d.loc[221,["Open","Low","High","Close"]]=[979.,970.,1090.,980.]
        report,trades=simulate_delivery(
            d,220,len(d), capital=100000, risk_fraction=.01,
        )
        self.assertEqual(report["Position_Risk_Fraction"],.01)
        self.assertEqual(len(trades),1)
        t=trades[0]
        self.assertEqual(t["Reason"],"stop")
        self.assertLessEqual(
            t["Projected_No_Gap_Stop_Loss_Rs"],t["Risk_Budget_Rs"]+1e-7,
        )
        self.assertAlmostEqual(t["Risk_Budget_Rs"],1000)
        self.assertLess(t["Position_Entry_Fraction"],1.0)
        self.assertGreater(t["All_Trading_Charges_Rs"],0)
        self.assertAlmostEqual(
            t["Net_Profit_Rs"],
            t["Sell_Turnover_Rs"]-t["Buy_Turnover_Rs"]
            -t["All_Trading_Charges_Rs"],
        )

    def test_gap_can_exceed_one_percent_budget(self):
        d=bars()
        d.loc[221,["Open","Low","High","Close"]]=[500.,480.,1100.,510.]
        r,trades=simulate_delivery(d,220,len(d),capital=100000,
                                   risk_fraction=.01)
        self.assertEqual(r["Completed_Trades"],1)
        self.assertEqual(r["Realised_Risk_Budget_Breaches"],1)
        t=trades[0]
        self.assertTrue(t["Gap_Below_Stop"])
        self.assertTrue(t["Risk_Budget_Exceeded_On_Exit"])
        self.assertLess(t["Net_Profit_Rs"],-1000.)
        self.assertLessEqual(t["Projected_No_Gap_Stop_Loss_Rs"],1000.+1e-7)

    def test_constrained_model_uses_fewer_shares_than_original(self):
        d=bars()
        d.loc[221,["Open","Low","High","Close"]]=[979.,960.,1100.,980.]
        base,old=simulate_delivery(d,220,len(d),capital=100000)
        sized,new=simulate_delivery(d,220,len(d),capital=100000,
                                   risk_fraction=.01)
        self.assertEqual(len(old),1)
        self.assertEqual(len(new),1)
        self.assertLess(new[0]["Quantity"],old[0]["Quantity"])
        self.assertLess(
            abs(new[0]["Net_Profit_Rs"]),abs(old[0]["Net_Profit_Rs"])
        )
        self.assertIsNone(old[0]["Risk_Budget_Rs"])
        self.assertIsNone(base["Position_Risk_Fraction"])

    def test_non_affordable_1pct_risk_position_skips_entire_entry(self):
        d=bars()
        result,trades=simulate_delivery(d,220,len(d),capital=1200,
                                        risk_fraction=.01)
        self.assertEqual(trades,[])
        self.assertEqual(result["Completed_Trades"],0)
        self.assertEqual(result["Final_Marked_Capital_Rs"],1200.)

    def test_invalid_fraction_rejected(self):
        for fraction in (0,-.01,.051,True,float("nan"),
                         float("inf"),"0.01"):
            with self.subTest(fraction=fraction):
                with self.assertRaises(ValueError):
                    simulate_delivery(bars(),220,225,risk_fraction=fraction)

    def test_report_is_read_only_and_distinguishes_three_scenarios(self):
        stock=long_stock()
        fake={
            "Estimated_All_Trading_Charges_Rs":25.,
            "Max_Drawdown":.03,
            "Final_Marked_Capital_Rs":99000.,
            "Realised_Risk_Budget_Breaches":0,
        }
        with patch("swing_risk_sizing_research.simulate_delivery",
                   return_value=(fake,[])) as sim:
            result=compare(stock,"SYNTHETIC_DIGEST",limit=1)
        self.assertEqual(sim.call_count,3*len(SCENARIOS))
        self.assertEqual(result["scope"],
                         "EXPOSURE_RISK_AUDIT_ONLY_NOT_STRATEGY_VALIDATION")
        self.assertEqual(result["top_three_profitable"],[])
        self.assertEqual(result["verified_profitable_strategies"],0)
        self.assertFalse(result["live_eligible"])
        self.assertEqual(result["orders"],"DISABLED")
        for i,call in enumerate(sim.call_args_list):
            self.assertEqual(call.kwargs["risk_fraction"],
                             SCENARIOS[i%3][1]["risk_fraction"])
            self.assertEqual(call.kwargs["capital"],100000.)
        for scenarios in result["summary"].values():
            for item in scenarios.values():
                self.assertIsNone(item["completed_mean_position_return_pct"])
                self.assertIsNone(
                    item["completed_mean_pre_trade_capital_return_pct"]
                )

    def test_edited_post_cutoff_bars_cannot_change_retrospective_summary(self):
        stock=long_stock()
        future=stock.iloc[-1:].copy()
        future["Date"]=pd.Timestamp("2026-10-16")
        future["Open"]=future["High"]=future["Close"]=1e8
        future["Low"]=99999999.
        fake={
            "Estimated_All_Trading_Charges_Rs":0.,
            "Max_Drawdown":0.,
            "Final_Marked_Capital_Rs":100000.,
            "Realised_Risk_Budget_Breaches":0,
        }
        with patch("swing_risk_sizing_research.simulate_delivery",
                   return_value=(fake,[])):
            a=compare(stock,"TEST",1)
            b=compare(pd.concat([stock,future]),"TEST",1)
        self.assertEqual(a["summary"],b["summary"])


if __name__=="__main__":
    unittest.main()
