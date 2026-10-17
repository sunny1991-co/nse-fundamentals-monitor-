"""No live network: official NSE discovery opt-in to annual XBRL catch-up."""
from datetime import datetime, timedelta, timezone
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from nse_annual_history import annual_target_symbols
from research_monitor.discovered_annual_universe import (
    verified_discovered_symbols, DiscoveredUniverseUnavailable,
)
from nse_mainboard_discovery import CATALOG, STATUS, EQUITY_URL, SME_URL


class OfficialAnnualCatchupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 10, 17, 9, tzinfo=timezone.utc)
        self.catalog = [
            {"Symbol": "BASE", "ISIN": "INE001A01011", "Company": "Base", "Series": "EQ"},
            {"Symbol": "NEW", "ISIN": "INE002A01012", "Company": "New", "Series": "EQ"},
        ]
        for path, names in [
            ("FY2425.csv", ["BASE"]),
            ("NSE_FY2025-26_Consolidated_Fundamentals.csv", ["BASE"]),
        ]:
            with (self.root/path).open("w", newline="", encoding="utf-8") as stream:
                w = csv.DictWriter(stream, fieldnames=["Symbol"])
                w.writeheader()
                for name in names:
                    w.writerow({"Symbol": name})
        self.write_catalog()
        (self.root/"runtime").mkdir()
        self.write_status()
        self.patcher = patch("research_monitor.discovered_annual_universe.MIN_MAINBOARD", 1)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def write_catalog(self):
        with (self.root/CATALOG).open("w", newline="", encoding="utf-8") as stream:
            w = csv.DictWriter(stream, fieldnames=["Symbol", "ISIN", "Company", "Series"])
            w.writeheader()
            w.writerows(self.catalog)

    def write_status(self):
        (self.root/STATUS).write_text(json.dumps({
            "state": "OFFICIAL_DISCOVERY_UPDATED",
            "checked_at_utc": self.now.isoformat(),
            "mainboard_eq_count": len(self.catalog),
            "equity_url": EQUITY_URL,
            "sme_url": SME_URL,
        }), encoding="utf-8")

    def test_discovered_is_not_added_without_optin(self):
        old = pd.DataFrame([{"Symbol": "OLDER"}])
        self.assertEqual(
            annual_target_symbols(self.root, old, include_discovered=False),
            {"BASE", "OLDER"},
        )

    def test_optin_adds_verified_mainboard_not_trading_permission(self):
        targets = annual_target_symbols(
            self.root, pd.DataFrame(), include_discovered=True, now=self.now
        )
        self.assertEqual(targets, {"BASE", "NEW"})
        self.assertFalse((self.root/"final_fundamental_shortlist.csv").exists())
        self.assertFalse((self.root/"swing_watchlist.json").exists())

    def test_stale_source_blocks_optin_but_not_base(self):
        older = self.now + timedelta(days=4)
        self.assertEqual(annual_target_symbols(self.root, pd.DataFrame()), {"BASE"})
        with self.assertRaisesRegex(DiscoveredUniverseUnavailable, "48_HOURS"):
            annual_target_symbols(self.root, pd.DataFrame(),
                                  include_discovered=True, now=older)

    def test_mismatched_isin_is_blocked(self):
        self.catalog[1]["ISIN"] = self.catalog[0]["ISIN"]
        self.write_catalog()
        with self.assertRaisesRegex(DiscoveredUniverseUnavailable, "AMBIGUOUS"):
            verified_discovered_symbols(self.root, self.now)

    def test_malformed_eq_series_is_blocked(self):
        self.catalog[1]["Series"] = "SM"
        self.write_catalog()
        with self.assertRaisesRegex(DiscoveredUniverseUnavailable, "AMBIGUOUS"):
            verified_discovered_symbols(self.root, self.now)

    def test_catalog_count_mismatch_is_blocked(self):
        self.catalog.pop()
        self.write_catalog()
        with self.assertRaisesRegex(DiscoveredUniverseUnavailable, "COUNT_MISMATCH"):
            verified_discovered_symbols(self.root, self.now)

    def test_missing_official_sme_verification_blocks_optin(self):
        (self.root/STATUS).write_text(json.dumps({
            "state": "OFFICIAL_DISCOVERY_UPDATED",
            "checked_at_utc": self.now.isoformat(),
            "mainboard_eq_count": len(self.catalog),
            "equity_url": EQUITY_URL,
        }), encoding="utf-8")
        with self.assertRaisesRegex(DiscoveredUniverseUnavailable, "NOT_OFFICIALLY_VERIFIED"):
            verified_discovered_symbols(self.root, self.now)


if __name__ == "__main__":
    unittest.main()
