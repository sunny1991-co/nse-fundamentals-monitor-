import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import fast_fundamental_screen as fast


class FastFundamentalScreenTests(unittest.TestCase):
    def test_two_year_verified_growth_can_pass_without_touching_strict_shortlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            quality = root / "annual_fundamental_quality_report.csv"
            fy24 = root / "FY2425.csv"
            fy25 = root / "NSE_FY2025-26_Consolidated_Fundamentals.csv"
            output = root / "fast_fundamental_shortlist.csv"
            summary = root / "fast_fundamental_screen_summary.csv"

            pd.DataFrame([{
                "Symbol": "ABC",
                "Two_Year_Baseline_Data_Quality": "PASS",
            }]).to_csv(quality, index=False)

            base = {
                "Symbol": "ABC", "Company": "ABC Ltd",
                "Reporting_Type": "Audited Consolidated", "Status": "OK",
                "Revenue_Operations_Cr": 100, "Net_Profit_Cr": 10,
                "Basic_EPS_Rs": 5, "Total_Assets_Cr": 200,
                "Total_Equity_Cr": 100, "Total_Borrowings_Cr": 40,
            }
            pd.DataFrame([base]).to_csv(fy24, index=False)
            newer = dict(base)
            newer.update({
                "Revenue_Operations_Cr": 120, "Net_Profit_Cr": 13,
                "Basic_EPS_Rs": 6, "Total_Assets_Cr": 230,
                "Total_Equity_Cr": 115, "Total_Borrowings_Cr": 45,
            })
            pd.DataFrame([newer]).to_csv(fy25, index=False)

            with patch.object(fast, "QUALITY", quality), \
                 patch.object(fast, "FY24", fy24), \
                 patch.object(fast, "FY25", fy25), \
                 patch.object(fast, "OUTPUT", output), \
                 patch.object(fast, "SUMMARY", summary):
                fast.main()

            passed = pd.read_csv(output)
            self.assertEqual(passed.Symbol.tolist(), ["ABC"])
            self.assertTrue(bool(passed.iloc[0].Fast_Fundamental_Pass))
            self.assertEqual(
                passed.iloc[0].Research_Scope,
                "PROVISIONAL_LOWER_CONFIDENCE_RESEARCH_ONLY",
            )
            status = pd.read_csv(summary).iloc[0]
            self.assertEqual(int(status.Fast_Fundamental_Pass_Count), 1)

    def test_unverified_symbol_is_never_fast_passed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            quality = root / "annual_fundamental_quality_report.csv"
            fy24 = root / "FY2425.csv"
            fy25 = root / "NSE_FY2025-26_Consolidated_Fundamentals.csv"
            output = root / "fast_fundamental_shortlist.csv"
            summary = root / "fast_fundamental_screen_summary.csv"

            pd.DataFrame([{
                "Symbol": "ABC",
                "Two_Year_Baseline_Data_Quality": "FAIL",
            }]).to_csv(quality, index=False)
            pd.DataFrame([{"Symbol": "ABC"}]).to_csv(fy24, index=False)
            pd.DataFrame([{"Symbol": "ABC"}]).to_csv(fy25, index=False)

            with patch.object(fast, "QUALITY", quality), \
                 patch.object(fast, "FY24", fy24), \
                 patch.object(fast, "FY25", fy25), \
                 patch.object(fast, "OUTPUT", output), \
                 patch.object(fast, "SUMMARY", summary):
                fast.main()

            passed = pd.read_csv(output)
            self.assertTrue(passed.empty)
            status = pd.read_csv(summary).iloc[0]
            self.assertEqual(int(status.Fast_Fundamental_Pass_Count), 0)


if __name__ == "__main__":
    unittest.main()
