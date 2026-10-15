"""Historical NSE filing-index recovery never bypasses audited annual verification."""
import csv
import tempfile
import unittest
from pathlib import Path

import nse_legacy_annual_recovery as recovery


def write(path, rows):
    if not rows:
        raise ValueError("Empty fixture")
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class LegacyRecoveryTests(unittest.TestCase):
    def test_quarterly_or_untrusted_urls_never_count_as_annual_candidates(self):
        url = "https://nsearchives.nseindia.com/corporate/xbrl/financials.xml"
        valid = {
            "Period Ended": "31-Mar-2023",
            "Audited / Unaudited": "Audited",
            "Consolidated / Non-Consolidated": "Consolidated",
            "Period": "Annual",
            "XBRL": url,
        }
        self.assertEqual(recovery.audited_annual_consolidated(valid, "2023-03-31"), (True, ""))
        self.assertEqual(
            recovery.audited_annual_consolidated({**valid, "Period": "Fourth Quarter"}, "2023-03-31")[1],
            "QUARTERLY_IS_NOT_ANNUAL",
        )
        self.assertEqual(
            recovery.audited_annual_consolidated({**valid, "Audited / Unaudited": "Un-Audited"}, "2023-03-31")[1],
            "AUDITED_STATUS_NOT_EXPLICIT",
        )
        self.assertEqual(
            recovery.audited_annual_consolidated({**valid, "XBRL": "https://nsearchives.nseindia.com.evil.org/corporate/xbrl/a.xml"}, "2023-03-31")[1],
            "OFFICIAL_XBRL_XML_URL_MISSING",
        )
        self.assertFalse(recovery.official_xbrl_link("https://example.com/filing.xml"))

    def test_candidate_stays_pending_and_missing_year_remains_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "FY2425.csv", [
                {"Symbol": "TESTABC", "Company": " Test    Company Limited "},
                {"Symbol": "OTHER", "Company": "Other Limited"},
            ])
            write(root / "NSE_FY2025-26_Consolidated_Fundamentals.csv", [
                {"Symbol": "TESTABC", "Company": "Test Company Limited"}
            ])
            write(root / "nse_legacy_financial_results_2022-23.csv", [
                {
                    "Company Name": "TEST COMPANY LIMITED",
                    "Period Ended": "31-Mar-2023",
                    "Audited / Unaudited": "Audited",
                    "Consolidated / Non-Consolidated": "Consolidated",
                    "Period": "Annual",
                    "XBRL": "https://nsearchives.nseindia.com/corporate/xbrl/TESTABC_2023_WEB.xml",
                },
                {
                    "Company Name": "Other Limited",
                    "Period Ended": "31-Mar-2023",
                    "Audited / Unaudited": "Audited",
                    "Consolidated / Non-Consolidated": "Consolidated",
                    "Period": "Fourth Quarter",
                    "XBRL": "https://nsearchives.nseindia.com/corporate/xbrl/OTHER_2023_WEB.xml",
                },
            ])
            manifest, summary = recovery.build(root)
            lookup = {(r["Symbol"], r["Financial_Year"]): r for r in manifest}
            self.assertEqual(lookup[("TESTABC", "2022-23")]["Recovery_Status"], "PENDING_INDEPENDENT_XBRL_REVIEW")
            self.assertEqual(lookup[("OTHER", "2022-23")]["Recovery_Status"], "OFFICIAL_INDEX_EXPORT_REQUIRED")
            self.assertEqual(lookup[("TESTABC", "2023-24")]["Recovery_Status"], "OFFICIAL_INDEX_EXPORT_REQUIRED")
            self.assertEqual(summary[0]["Unverified_Index_Candidates"], 1)
            self.assertEqual(sum(r["Existing_XBRL_Verified"] for r in summary), 0)

    def test_multiple_revisions_never_auto_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "FY2425.csv", [{"Symbol": "TESTABC", "Company": "Test Company Limited"}])
            base = {
                "Symbol": "TESTABC",
                "Period Ended": "31-Mar-2024",
                "Audited / Unaudited": "Audited",
                "Consolidated / Non-Consolidated": "Consolidated",
                "Period": "Annual",
            }
            write(root / "nse_legacy_financial_results_2023-24.csv", [
                {**base, "XBRL": "https://nsearchives.nseindia.com/corporate/xbrl/A_WEB.xml"},
                {**base, "XBRL": "https://nsearchives.nseindia.com/corporate/xbrl/B_WEB.xml"},
            ])
            manifest, _ = recovery.build(root)
            row = next(r for r in manifest if r["Financial_Year"] == "2023-24")
            self.assertEqual(row["Recovery_Status"], "MULTIPLE_SOURCE_REVISIONS_NEED_REVIEW")
            self.assertEqual(row["Source_URL"].count(".xml"), 2)


if __name__ == "__main__":
    unittest.main()
