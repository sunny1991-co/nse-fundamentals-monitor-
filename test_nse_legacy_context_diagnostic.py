"""Synthetic-only tests: diagnostic cannot authenticate legacy financial facts."""
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock
import requests

from test_legacy_xbrl_verify import fixture, URL
from nse_legacy_context_diagnostic import (
    MAX_FILES, REPORT_NAME, inspect, diagnostic,
)
from research_monitor.legacy_xbrl import verify
from test_legacy_xbrl_verify import RECORD


class ContextDiagnosticUnitTests(unittest.TestCase):
    def test_known_exact_annual_context_is_visible_without_attestation(self):
        result = inspect(fixture(), symbol="TEST", fiscal_end="2023-03-31")
        self.assertEqual(
            result["reason"],
            "BASIC_CONTEXT_CONDITIONS_PRESENT_ADDITIONAL_AUDIT_REQUIRED",
        )
        self.assertEqual(result["counters"]["strict_duration_eligible_contexts"],1)
        self.assertEqual(result["counters"]["strict_instant_eligible_contexts"],1)
        self.assertFalse(result["verified_financial_facts"])

    def test_q4_falsely_labeled_annual_is_explained_and_stays_unverified(self):
        bad = fixture().replace(b"2022-04-01",b"2023-01-01")
        self.assertEqual(
            inspect(bad,symbol="TEST",fiscal_end="2023-03-31")["reason"],
            "EXACT_ANNUAL_DURATION_MISSING",
        )
        self.assertEqual(verify(RECORD,URL,bad)[1],
                         "EXACT_APRIL_MARCH_CONTEXT_NOT_VERIFIED")

    def test_company_identifier_scheme_failure_is_distinct_from_dates(self):
        bad=fixture().replace(
            b"http://www.nseindia.com/NSESymbol",
            b"https://example.invalid/other-id-scheme",
        )
        report=inspect(bad,symbol="TEST",fiscal_end="2023-03-31")
        self.assertEqual(
            report["reason"],
            "ANNUAL_DURATION_EXISTS_BUT_CANONICAL_NSE_ENTITY_MISSING",
        )
        self.assertEqual(
            report["counters"]["exact_april_march_duration_contexts"],1
        )
        self.assertEqual(
            report["counters"]["exact_duration_with_matching_nse_entity"],0
        )
        self.assertNotIn("example.invalid",json.dumps(report))
        self.assertEqual(verify(RECORD,URL,bad)[1],
                         "EXACT_APRIL_MARCH_CONTEXT_NOT_VERIFIED")

    def test_valid_period_but_missing_balance_sheet_context(self):
        bad=fixture().replace(
            b"<xbrli:instant>2023-03-31</xbrli:instant>",
            b"<xbrli:instant>2023-03-30</xbrli:instant>",
        )
        self.assertEqual(
            inspect(bad,symbol="TEST",fiscal_end="2023-03-31")["reason"],
            "ANNUAL_CONTEXT_PRESENT_BUT_YEAR_END_INSTANT_MISSING",
        )

    def test_dtd_html_and_bad_dates_rejected(self):
        for body in (
            b"<html>Bad gateway</html>",
            b"<!DOCTYPE doc>" + fixture(),
            b"", b"<broken",
        ):
            with self.assertRaises(ValueError):
                inspect(body,symbol="TEST",fiscal_end="2023-03-31")


class ContextDiagnosticCollectionTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.now=datetime(2026,10,17,3,30,tzinfo=timezone.utc)
        (self.root/"FY2425.csv").write_text("Symbol,Company\nTEST,Synthetic\n")
        fields=(
            "Symbol,Company Name,Period Ended,Audited / Unaudited,"
            "Consolidated / Non-Consolidated,Period,XBRL URL,Broadcast Date\n"
        )
        for year,end in (("2022-23","31-Mar-2023"),("2023-24","31-Mar-2024")):
            with (self.root/f"nse_legacy_financial_results_{year}.csv").open("w") as out:
                out.write(fields+(
                    f"TEST,Synthetic,{end},Audited,Consolidated,Annual,"
                    f"{URL if year == '2022-23' else URL.replace('TEST','TEST2')},"
                    f"01-May-{end[-4:]} 15:00:00\n"
                ))

    def test_diagnostics_never_update_history_or_strict_sector(self):
        history=self.root/"annual_historical_results.csv"
        history.write_text("Symbol,Financial_Year\nEXISTING,2025-26\n")
        prior=history.read_bytes()
        f=Mock(return_value=fixture())
        result=diagnostic(self.root,limit=1,session=Mock(),fetch=f,
                          pause=lambda _: None,now=self.now)
        self.assertEqual(result["checked_records"],1)
        self.assertEqual(f.call_count,1)
        self.assertEqual(history.read_bytes(),prior)
        self.assertFalse((self.root/"nse_sector_map.csv").exists())
        self.assertEqual(result["added_verified_financial_facts"],0)
        self.assertFalse(result["updated_annual_history"])
        self.assertFalse(result["changed_trade_eligibility"])
        self.assertEqual(result["orders"],"DISABLED")
        self.assertTrue((self.root/"runtime"/REPORT_NAME).is_file())

    def test_http_403_stops_without_attempting_next_record(self):
        denial=requests.HTTPError("HTTP_403",response=Mock(status_code=403))
        f=Mock(side_effect=denial)
        r=diagnostic(self.root,limit=MAX_FILES,session=Mock(),fetch=f,
                     pause=lambda _:None,now=self.now)
        self.assertEqual(r["state"],"STOPPED_OFFICIAL_ACCESS_BLOCKED")
        self.assertEqual(f.call_count,1)
        self.assertEqual(r["added_verified_financial_facts"],0)

    def test_limit_is_bounded_and_not_a_bulk_download(self):
        for n in (0,MAX_FILES+1,300):
            with self.assertRaisesRegex(ValueError,"DIAGNOSTIC_LIMIT"):
                diagnostic(self.root,limit=n,session=Mock(),now=self.now)


if __name__ == "__main__":
    unittest.main()
