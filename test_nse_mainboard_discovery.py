"""Offline regression tests: discovery must never silently admit SME or trades."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from nse_mainboard_discovery import (
    CATALOG, QUEUE, STATUS, EQUITY_URL, SME_URL,
    DiscoveryUnavailable, listing_rows, refresh,
)


def make_list(rows):
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["SYMBOL", "NAME OF COMPANY", "SERIES",
                     "DATE OF LISTING", "ISIN NUMBER", "FACE VALUE"])
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def security(symbol, isin, series="EQ"):
    return [symbol, "Company " + symbol, series, "01-JAN-2024", isin, "10"]


def write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        w = csv.DictWriter(handle, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


class NseMainboardDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 17, 6, tzinfo=timezone.utc)
        write_csv(self.root/"FY2425.csv", ["Symbol"], [{"Symbol": "OLD"}])
        self.main_rows = [
            security("OLD", "INE111A01011"),
            security("OTHER", "INE222A01012"),
            security("SMEX", "INE333A01013"),
            security("BEONLY", "INE444A01014", "BE"),
        ]
        self.sme_rows = [security("SMEX", "INE333A01013", "SM")]
        self.feed = {
            EQUITY_URL: make_list(self.main_rows),
            SME_URL: make_list(self.sme_rows),
        }

    def tearDown(self):
        self.temp.cleanup()

    def run_refresh(self):
        return refresh(self.root, fetch=self.feed.__getitem__,
                       min_main=2, min_sme=1, min_baseline=1, now=self.now)

    def test_filters_sme_and_non_eq_retains_untracked_candidate(self):
        status = self.run_refresh()
        self.assertEqual(status["mainboard_eq_count"], 2)
        self.assertEqual(status["sme_excluded"], 1)
        with (self.root/CATALOG).open(newline="") as f:
            catalog = list(csv.DictReader(f))
        self.assertEqual([row["Symbol"] for row in catalog], ["OLD", "OTHER"])
        outside = next(r for r in catalog if r["Symbol"] == "OTHER")
        self.assertEqual(outside["Baseline_Tracked"], "false")
        self.assertEqual(outside["Discovery_State"], "NEW_OR_UNTRACKED_RESEARCH_ONLY")
        self.assertFalse((self.root/"swing_watchlist.json").exists())
        self.assertFalse((self.root/"swing_trade_candidates.csv").exists())
        self.assertEqual(json.loads((self.root/STATUS).read_text())["orders"],
                         "DISABLED")

    def test_second_scan_is_idempotent_and_keeps_first_seen(self):
        self.run_refresh()
        self.now = datetime(2026, 10, 18, 6, tzinfo=timezone.utc)
        result = self.run_refresh()
        self.assertEqual(result["new_or_isin_changed_since_last_snapshot"], 0)
        with (self.root/CATALOG).open(newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(all(row["First_Seen_UTC"].startswith("2026-10-17")
                            for row in rows))
        self.assertTrue(all(row["New_Since_Prior_List"] == "false"
                            for row in rows))

    def test_missing_sme_list_preserves_last_verified_catalog(self):
        self.run_refresh()
        existing = (self.root/CATALOG).read_bytes()
        self.feed[SME_URL] = b"<html>Not a list</html>"
        with self.assertRaises(DiscoveryUnavailable):
            self.run_refresh()
        self.assertEqual((self.root/CATALOG).read_bytes(), existing)

    def test_rejects_duplicate_conflicting_identifiers(self):
        rows = [security("ONE", "INE111A01011"),
                security("TWO", "INE111A01011")]
        with self.assertRaisesRegex(DiscoveryUnavailable, "CONFLICTING"):
            listing_rows(make_list(rows), minimum=1, maximum=5, is_sme=False)

    def test_rejects_large_unexpected_universe_drop(self):
        self.run_refresh()
        self.main_rows.append(security("THIRD", "INE555A01015"))
        self.feed[EQUITY_URL] = make_list(self.main_rows)
        with self.assertRaisesRegex(DiscoveryUnavailable, "SUDDEN_CHANGE"):
            self.run_refresh()

    def test_prior_catalog_is_not_a_trading_permission(self):
        write_csv(self.root/"annual_fundamental_quality_report.csv",
                  ["Symbol", "Full_Fundamental_Criteria_Pass"],
                  [{"Symbol": "OLD", "Full_Fundamental_Criteria_Pass": "true"}])
        write_csv(self.root/"final_fundamental_shortlist.csv", ["Symbol"],
                  [{"Symbol": "OLD"}])
        self.run_refresh()
        with (self.root/QUEUE).open(newline="") as f:
            q = list(csv.DictReader(f))
        self.assertEqual([r["Symbol"] for r in q], ["OTHER"])
        with (self.root/CATALOG).open(newline="") as f:
            c = {r["Symbol"]: r for r in csv.DictReader(f)}
        self.assertEqual(c["OLD"]["Discovery_State"],
                         "EXISTING_STRICT_FUNDAMENTAL_SHORTLIST_ONLY")
        self.assertEqual(c["OTHER"]["Strict_Annual_Pass"], "false")

    def test_sme_isin_overlap_cannot_enter_mainboard(self):
        self.sme_rows = [security("OTHER_SME", "INE222A01012", "SM")]
        self.feed[SME_URL] = make_list(self.sme_rows)
        self.run_refresh()
        with (self.root/CATALOG).open(newline="") as f:
            result = {r["Symbol"] for r in csv.DictReader(f)}
        self.assertNotIn("OTHER", result)
        self.assertEqual(result, {"OLD", "SMEX"})


if __name__ == "__main__":
    unittest.main()
