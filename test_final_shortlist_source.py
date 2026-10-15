"""Fail-closed fundamental shortlist tests for live NSE collector availability."""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import final_fundamental_shortlist as screen


class QuarterlySourceSafetyTests(unittest.TestCase):
    def test_collector_source_must_be_ok_and_recent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.csv"
            ok, reason = screen.quarterly_source_readiness(path)
            self.assertFalse(ok)
            self.assertEqual(reason, "QUARTERLY_SOURCE_STATUS_MISSING")

            for status, run_date, expected in [
                ("NSE_XBRL_ACCESS_BLOCKED", date.today().isoformat(), "QUARTERLY_SOURCE_ACCESS_NOT_VERIFIED"),
                ("OK", (date.today() - timedelta(days=3)).isoformat(), "QUARTERLY_SOURCE_STATUS_STALE"),
                ("OK", date.today().isoformat(), "OK"),
            ]:
                pd.DataFrame([{"Run_Date": run_date, "Collection_Status": status}]).to_csv(path, index=False)
                ready, reason = screen.quarterly_source_readiness(path)
                self.assertEqual(ready, expected == "OK")
                self.assertEqual(reason, expected)

    def test_blocked_collector_excludes_even_an_annual_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            annual = root / "annual.csv"
            status = root / "source.csv"
            shortlist = root / "shortlist.csv"
            excluded = root / "excluded.csv"
            summary = root / "summary.csv"
            pd.DataFrame([{
                "Symbol": "TESTABC", "Company": "Test Company",
                "Full_Fundamental_Criteria_Pass": True,
                "Full_Screen_Blockers": "",
            }]).to_csv(annual, index=False)
            pd.DataFrame([{
                "Run_Date": date.today().isoformat(),
                "Collection_Status": "NSE_XBRL_ACCESS_BLOCKED",
            }]).to_csv(status, index=False)

            with (
                patch.object(screen, "ANNUAL_FILE", annual),
                patch.object(screen, "QUARTERLY_FILE", root / "missing_quarters.csv"),
                patch.object(screen, "QUARTERLY_SOURCE_STATUS_FILE", status),
                patch.object(screen, "SHORTLIST_FILE", shortlist),
                patch.object(screen, "EXCLUSIONS_FILE", excluded),
                patch.object(screen, "SUMMARY_FILE", summary),
            ):
                screen.main()

            self.assertEqual(len(pd.read_csv(shortlist)), 0)
            row = pd.read_csv(excluded).iloc[0]
            self.assertIn("QUARTERLY_SOURCE_ACCESS_NOT_VERIFIED", row["Final_Failure_Reasons"])
            self.assertFalse(bool(pd.read_csv(summary).iloc[0]["Quarterly_Source_Gate_Pass"]))


if __name__ == "__main__":
    unittest.main()
