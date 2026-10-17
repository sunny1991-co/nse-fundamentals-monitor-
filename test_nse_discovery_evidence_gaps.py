"""Regression tests for read-only per-company evidence coverage."""
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nse_mainboard_discovery import CATALOG, STATUS
from nse_discovery_evidence_gaps import (
    OUTPUT, RESULT, EvidenceGapError, snapshot,
)


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


class DiscoveryCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 17, 8, tzinfo=timezone.utc)
        listing = []
        for index, sym in enumerate(("BASE", "OUT", "GROW"), start=1):
            listing.append({
                "Symbol": sym, "ISIN": f"INE{index:03d}A01011",
                "Company": sym + " Company", "Series": "EQ",
                "Baseline_Tracked": "false" if sym == "OUT" else "true",
            })
        write_csv(self.root/CATALOG,
                  ["Symbol", "ISIN", "Company", "Series", "Baseline_Tracked"], listing)
        runtime = self.root/"runtime"
        runtime.mkdir()
        (self.root/STATUS).write_text(json.dumps({
            "checked_at_utc": self.now.isoformat(),
            "state": "OFFICIAL_DISCOVERY_UPDATED",
            "mainboard_eq_count": len(listing),
        }))
        write_csv(self.root/"FY2425.csv", ["Symbol"],
                  [{"Symbol": "BASE"}, {"Symbol": "GROW"}])
        write_csv(self.root/"NSE_FY2025-26_Consolidated_Fundamentals.csv", ["Symbol"],
                  [{"Symbol": "BASE"}, {"Symbol": "GROW"}])
        write_csv(self.root/"annual_fundamental_quality_report.csv",
                  ["Symbol", "Full_Fundamental_Criteria_Pass"],
                  [{"Symbol": "BASE", "Full_Fundamental_Criteria_Pass": "true"},
                   {"Symbol": "GROW", "Full_Fundamental_Criteria_Pass": "false"}])
        write_csv(self.root/"final_fundamental_shortlist.csv", ["Symbol"],
                  [{"Symbol": "BASE"}])
        write_csv(self.root/"nse_eod_history_coverage.csv",
                  ["Symbol", "Completed_Bars", "Unsupported_Complex_Action"],
                  [{"Symbol": "OUT", "Completed_Bars": "200",
                    "Unsupported_Complex_Action": "false"}])
        (self.root/"swing_watchlist.json").write_text("PRESERVE")

    def tearDown(self):
        self.temp.cleanup()

    def run_snapshot(self):
        with patch("nse_discovery_evidence_gaps.MIN_UNIVERSE", 2):
            return snapshot(self.root, now=self.now)

    def test_research_queue_shows_outside_and_missing_filing_years(self):
        report = self.run_snapshot()
        self.assertEqual(report["mainboard_companies"], 3)
        self.assertEqual(report["outside_annual_baseline"], 1)
        self.assertEqual(report["missing_at_least_one_fy_row"], 1)
        with (self.root/OUTPUT).open(newline="") as f:
            rows = {r["Symbol"]: r for r in csv.DictReader(f)}
        self.assertEqual(rows["OUT"]["Research_State"], "NEEDS_AUDITED_ANNUAL_DATA")
        self.assertEqual(rows["OUT"]["Local_EOD_Completed_Bars_Unattested"], "200")
        self.assertEqual(rows["GROW"]["Research_State"], "STRICT_FUNDAMENTALS_NOT_PASSED")
        self.assertEqual(rows["BASE"]["Research_State"], "EXISTING_FUNDAMENTAL_RESEARCH_ONLY")
        self.assertTrue(all(r["Trade_Eligible"] == "false" for r in rows.values()))
        self.assertEqual((self.root/"swing_watchlist.json").read_text(), "PRESERVE")

    def test_stale_official_source_does_not_replace_previous_report(self):
        self.run_snapshot()
        original = (self.root/OUTPUT).read_bytes()
        self.now += timedelta(days=5)
        with self.assertRaisesRegex(EvidenceGapError, "STALE"):
            self.run_snapshot()
        self.assertEqual((self.root/OUTPUT).read_bytes(), original)

    def test_catalog_mismatch_fails_closed(self):
        self.run_snapshot()
        existing = (self.root/OUTPUT).read_bytes()
        rows = list(csv.DictReader((self.root/CATALOG).open()))
        write_csv(self.root/CATALOG,
                  ["Symbol", "ISIN", "Company", "Series", "Baseline_Tracked"],
                  rows[:2])
        with self.assertRaisesRegex(EvidenceGapError, "COUNT_MISMATCH"):
            self.run_snapshot()
        self.assertEqual(existing, (self.root/OUTPUT).read_bytes())

    def test_ambiguous_isin_is_not_accepted(self):
        rows = list(csv.DictReader((self.root/CATALOG).open()))
        rows[1]["ISIN"] = rows[0]["ISIN"]
        write_csv(self.root/CATALOG,
                  ["Symbol", "ISIN", "Company", "Series", "Baseline_Tracked"], rows)
        with self.assertRaisesRegex(EvidenceGapError, "CONFLICT"):
            self.run_snapshot()

    def test_absent_official_status_blocks_unverified_scan(self):
        (self.root/STATUS).unlink()
        with self.assertRaisesRegex(EvidenceGapError, "NOT_YET_COMPLETED"):
            self.run_snapshot()
        self.assertFalse((self.root/OUTPUT).exists())


if __name__ == "__main__":
    unittest.main()
