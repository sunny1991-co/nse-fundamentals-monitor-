"""Regression tests: raw quarterly collection must not relax annual trading gates."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import nse_quarterly_monitor as quarterly


class QuarterlyCollectionTests(unittest.TestCase):
    def test_zero_annual_pass_still_collects_verified_history_without_signals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            annual = root / "annual_fundamental_quality_report.csv"
            raw = root / "quarterly_results.csv"
            changes = root / "quarterly_change_report.csv"
            unverified = root / "quarterly_unverified_filings.csv"
            processed = root / "quarterly_processed_filings.csv"
            pd.DataFrame([
                {"Symbol": "TESTABC", "Full_Fundamental_Criteria_Pass": False}
            ]).to_csv(annual, index=False)

            url = "https://nsearchives.nseindia.com/corporate/xbrl/TESTABC_WEB.xml"
            record = {
                "symbol": "TESTABC", "qe_Date": "2026-06-30",
                "consolidated": "Consolidated", "xbrl": url,
            }
            response = Mock(status_code=200, content=b"<test/>")
            response.raise_for_status.return_value = None
            session = Mock()
            session.get.return_value = response
            row = {
                "Symbol": "TESTABC", "Quarter_End": "2026-06-30",
                "Reporting_Basis": "Consolidated",
                "Revenue_Operations_Cr": 100.0, "Net_Profit_Cr": 15.0,
                "Basic_EPS_Rs": 2.0, "Total_Assets_Cr": 200.0,
                "Total_Equity_Cr": 100.0, "Total_Borrowings_Cr": 10.0,
                "Missing_Fields": "", "Fundamentals_Complete": True,
                "Filing_Published_At": "2026-07-15", "Source_URL": url,
            }
            with (
                patch.object(quarterly, "ANNUAL_QUALITY_FILE", annual),
                patch.object(quarterly, "RESULTS_FILE", raw),
                patch.object(quarterly, "COMPARE_FILE", changes),
                patch.object(quarterly, "UNVERIFIED_FILE", unverified),
                patch.object(quarterly, "PROCESSED_FILE", processed),
                patch.object(quarterly, "make_session", return_value=session) as connect,
                patch.object(quarterly, "fetch_recent_records", return_value=[record]),
                patch.object(quarterly, "parse_xbrl", return_value=(row, "")) as parse,
                patch.object(quarterly.time, "sleep", return_value=None),
            ):
                quarterly.main()

            connect.assert_called_once()
            parse.assert_called_once()
            self.assertEqual(len(pd.read_csv(raw)), 1)
            self.assertEqual(len(pd.read_csv(changes)), 0)
            self.assertEqual(pd.read_csv(processed).iloc[0]["Status"], "VERIFIED")

    def test_missing_annual_report_blocks_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(quarterly, "ANNUAL_QUALITY_FILE", Path(directory) / "missing.csv"):
                with self.assertRaisesRegex(RuntimeError, "Annual quality report is missing"):
                    quarterly.main()


if __name__ == "__main__":
    unittest.main()
