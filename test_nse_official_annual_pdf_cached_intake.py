"""Cached-index-only official NSE PDF source intake; hard no-metadata-API gates."""
import csv
import json
import tempfile
from datetime import datetime,timezone,timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

from nse_mainboard_discovery import CATALOG
from nse_official_annual_pdf_auto_index import (
    offline_cached_index_intake,CACHED_STATUS,STATUS,OUTPUT,OUTPUT_FIELDS,
    PDF_LOG,PDF_DIR,CACHED_ATTEMPTS,CACHED_ATTEMPT_FIELDS,
    source_access_cooldown,OfficialAnnualPdfUnavailable,
    parse_metadata,acquire_pdfs,main,
)
from test_nse_official_annual_pdf_auto_index import MockSession,pdf_bytes

NOW=datetime(2026,10,17,12,30,tzinfo=timezone.utc)
PDF_A="https://nsearchives.nseindia.com/annual_reports/AR_123_NEW_2022_2023_A_123.pdf"
PDF_B="https://nsearchives.nseindia.com/annual_reports/AR_123_NEW_2023_2024_A_456.pdf"


def csvwrite(path,fields,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class CachedOfficialPDFIntakeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        csvwrite(self.root/CATALOG,[
            "Symbol","ISIN","Company","Baseline_Tracked","Series"],[{
            "Symbol":"NEW","ISIN":"INE001A01011","Company":"New Ltd",
            "Baseline_Tracked":"true","Series":"EQ",
        }])
        self.validated=patch(
            "nse_official_annual_pdf_auto_index.verified_discovered_symbols",
            return_value={"NEW"})
        self.validated.start()
        self.addCleanup(self.validated.stop)
        self.review_validated=patch(
            "nse_annual_pdf_section_review.verified_discovered_symbols",
            return_value={"NEW"})
        self.review_validated.start()
        self.addCleanup(self.review_validated.stop)
        source={
            "data":[
                {"fromYr":2022,"toYr":2023,"fileName":PDF_A},
                {"fromYr":2023,"toYr":2024,"fileName":PDF_B},
            ]
        }
        self.entries=parse_metadata(source,"NEW","INE001A01011",
                                    "New Ltd",NOW.isoformat())
        csvwrite(self.root/OUTPUT,OUTPUT_FIELDS,self.entries)
        self.blocked={
            "checked_at_utc":NOW.isoformat(),
            "state":"NSE_OFFICIAL_ANNUAL_INDEX_ACCESS_BLOCKED",
        }
        (self.root/STATUS).parent.mkdir(parents=True,exist_ok=True)
        (self.root/STATUS).write_text(json.dumps(self.blocked))
        self.protected={
            "annual_historical_results.csv":b"EXISTING_AUDITED_HISTORY",
            "final_fundamental_shortlist.csv":b"EXISTING_NO_TRADE_SCREEN",
            "swing_watchlist.json":b'{"orders":"DISABLED"}',
            "market_ohlcv.csv":b"EXISTING_OHLCV",
        }
        for path,contents in self.protected.items():
            (self.root/path).write_bytes(contents)

    def untouched(self):
        for path,contents in self.protected.items():
            self.assertEqual((self.root/path).read_bytes(),contents)
        self.assertEqual(json.loads((self.root/STATUS).read_text()),
                         self.blocked)

    def test_offline_inventory_calls_no_http_and_preserves_existing_api_cooldown(self):
        session=MockSession({"data":[]})
        with patch("nse_official_annual_pdf_auto_index.requests.Session",
                   side_effect=AssertionError("NO SESSION FOR OFFLINE PLAN")):
            out=offline_cached_index_intake(self.root,downloads=0,
                                            now=NOW,session=session)
        self.assertEqual(out["official_nse_mainboard_eq_companies"],1)
        self.assertEqual(out["previously_indexed_official_pdf_candidate_links"],2)
        self.assertEqual(out["validated_indexed_pdf_links_pending_archive"],2)
        self.assertEqual(out["already_archived_original_pdf_receipts"],0)
        self.assertEqual(out["annual_report_metadata_api_calls_this_run"],0)
        self.assertEqual(out["old_financial_results_api_calls_this_run"],0)
        self.assertEqual(out["pdf_archive_requests_this_run"],0)
        self.assertEqual(out["new_strict_fundamental_passes"],0)
        self.assertEqual(out["orders"],"DISABLED")
        self.assertEqual(session.api_calls,0)
        self.assertEqual(session.pdf_calls,0)
        self.assertFalse((self.root/PDF_LOG).exists())
        self.assertTrue((self.root/CACHED_STATUS).exists())
        self.assertEqual(source_access_cooldown(self.root,NOW),
                         (NOW+timedelta(hours=20)).isoformat())
        self.untouched()

    def test_cached_direct_one_pdf_download_no_metadata_api_or_fact_promotion(self):
        session=MockSession({"data":[]})
        out=offline_cached_index_intake(self.root,downloads=1,
                                        now=NOW,session=session)
        self.assertEqual(session.pdf_calls,1)
        self.assertEqual(session.api_calls,0)
        self.assertEqual(out["new_archived_pdf_candidates_this_run"],1)
        self.assertEqual(out["validated_indexed_pdf_links_pending_archive"],1)
        with (self.root/PDF_LOG).open(newline="") as file:
            receipt=next(csv.DictReader(file))
        self.assertEqual(receipt["Official_Report_URL"],PDF_B)
        self.assertIn("REQUIRES_AUDITED_CONSOLIDATED",
                      receipt["Review_Status"])
        self.assertEqual((self.root/STATUS).read_text(),
                         json.dumps(self.blocked))
        second=offline_cached_index_intake(self.root,downloads=0,
                                           now=NOW,session=session)
        self.assertEqual(second["already_archived_original_pdf_receipts"],1)
        self.assertEqual(second["validated_indexed_pdf_links_pending_archive"],1)
        self.untouched()

    def test_invalid_isin_malicious_pdf_url_wrong_fy_and_duplicates_block(self):
        for column,value,reason in [
            ("ISIN","INE000000000","ISSUER_ISIN"),
            ("Official_Report_URL","https://evil.invalid/a.pdf","SOURCE_UNTRUSTED"),
            ("Financial_Year","2021-22","SOURCE_UNTRUSTED"),
            ("Original_Metadata_SHA256","bad","SOURCE_UNTRUSTED"),
            ("From_Year","2021","FY_NOT_SOURCE_ATTESTED"),
            ("Verification_State","ALREADY_AUDITED","SOURCE_UNTRUSTED"),
        ]:
            with self.subTest(field=column):
                row=dict(self.entries[0])
                row[column]=value
                csvwrite(self.root/OUTPUT,OUTPUT_FIELDS,[row])
                with self.assertRaisesRegex(OfficialAnnualPdfUnavailable,reason):
                    offline_cached_index_intake(self.root,downloads=0,now=NOW)
                self.assertFalse((self.root/PDF_LOG).exists())
                self.untouched()
        csvwrite(self.root/OUTPUT,OUTPUT_FIELDS,[self.entries[0]]*2)
        with self.assertRaisesRegex(OfficialAnnualPdfUnavailable,
                                    "DUPLICATE_OR_AMBIGUOUS"):
            offline_cached_index_intake(self.root,downloads=0,now=NOW)
        self.untouched()

    def test_automatic_cached_source_has_hard_request_budget_even_on_404(self):
        csvwrite(self.root/OUTPUT,OUTPUT_FIELDS,self.entries)
        class FirstMissing(MockSession):
            def get(self,url,**kwargs):
                if "/annual_reports/" in url:
                    self.pdf_calls+=1
                    from test_nse_official_annual_pdf_auto_index import MockResponse
                    return MockResponse(b"",404)
                return super().get(url,**kwargs)
        s=FirstMissing({"data":[]})
        out=offline_cached_index_intake(self.root,downloads=1,
                                        now=NOW,session=s)
        self.assertEqual(s.pdf_calls,1)
        self.assertEqual(s.api_calls,0)
        self.assertEqual(out["new_archived_pdf_candidates_this_run"],0)
        self.assertFalse((self.root/PDF_LOG).exists())
        self.untouched()

    def test_404_pdf_link_is_deferred_without_blocking_other_original_pdfs(self):
        from test_nse_official_annual_pdf_auto_index import MockResponse
        class Missing2024(MockSession):
            def get(self,url,**kwargs):
                if "/annual_reports/" in url and url==PDF_B:
                    self.pdf_calls+=1
                    return MockResponse(b"",404)
                return super().get(url,**kwargs)
        provider=Missing2024({"data":[]})
        first=offline_cached_index_intake(self.root,downloads=1,
                                          now=NOW,session=provider)
        self.assertEqual(provider.pdf_calls,1)
        self.assertEqual(first["new_archived_pdf_candidates_this_run"],0)
        self.assertEqual(first["pdf_archive_requests_this_run"],1)
        self.assertFalse((self.root/PDF_LOG).exists())
        with (self.root/CACHED_ATTEMPTS).open(newline="") as handle:
            first_attempt=next(csv.DictReader(handle))
        self.assertEqual(first_attempt["Official_Report_URL"],PDF_B)
        self.assertEqual(first_attempt["Attempt_Result"],"PDF_NOT_FOUND_404")
        second=offline_cached_index_intake(self.root,downloads=1,
                                           now=NOW+timedelta(hours=1),
                                           session=provider)
        self.assertEqual(provider.pdf_calls,2)
        self.assertEqual(second["indexed_pdf_links_deferred_by_pdf_source_retry_guard"],1)
        self.assertEqual(second["new_archived_pdf_candidates_this_run"],1)
        with (self.root/PDF_LOG).open(newline="") as handle:
            self.assertEqual(next(csv.DictReader(handle))["Official_Report_URL"],PDF_A)
        third=offline_cached_index_intake(self.root,downloads=2,
                                          now=NOW+timedelta(hours=2),
                                          session=provider)
        self.assertEqual(third["state"],
                         "CACHED_NSE_ANNUAL_PDF_DOWNLOAD_RETRY_COOLDOWN_NO_REQUEST")
        self.assertEqual(third["pdf_archive_requests_this_run"],0)
        self.assertEqual(provider.pdf_calls,2)
        self.assertEqual(provider.api_calls,0)
        self.untouched()

    def test_403_from_one_original_pdf_cools_down_entire_archive_host(self):
        from test_nse_official_annual_pdf_auto_index import MockResponse
        class Denied(MockSession):
            def get(self,url,**kwargs):
                if "/annual_reports/" in url:
                    self.pdf_calls+=1
                    return MockResponse(b"",403)
                return super().get(url,**kwargs)
        blocked=Denied({"data":[]})
        initial=offline_cached_index_intake(self.root,downloads=2,
                                            now=NOW,session=blocked)
        self.assertEqual(initial["new_archived_pdf_candidates_this_run"],0)
        self.assertEqual(initial["pdf_archive_requests_this_run"],1)
        self.assertEqual(blocked.pdf_calls,1)
        self.assertFalse((self.root/PDF_LOG).exists())
        ordinary=MockSession({"data":[]})
        within=offline_cached_index_intake(
            self.root,downloads=2,now=NOW+timedelta(hours=2),session=ordinary)
        self.assertEqual(within["state"],
                         "CACHED_NSE_ANNUAL_PDF_DOWNLOAD_RETRY_COOLDOWN_NO_REQUEST")
        self.assertEqual(within["archived_pdf_source_host_cooldown_until_utc"],
                         (NOW+timedelta(hours=20)).isoformat())
        self.assertEqual(within["pdf_archive_requests_this_run"],0)
        self.assertEqual(ordinary.pdf_calls,0)
        after=offline_cached_index_intake(
            self.root,downloads=1,now=NOW+timedelta(hours=21),session=ordinary)
        self.assertEqual(after["pdf_archive_requests_this_run"],1)
        self.assertEqual(after["new_archived_pdf_candidates_this_run"],1)
        self.assertEqual(ordinary.pdf_calls,1)
        self.assertEqual(ordinary.api_calls,0)
        self.untouched()

    def test_cached_attempt_ledger_identity_forgery_fails_closed_without_http(self):
        rows=[{
            "Symbol":"NEW","Financial_Year":"2023-24",
            "Official_Report_URL":PDF_B,
            "Original_Metadata_SHA256":"0"*64,
            "Attempted_At_UTC":NOW.isoformat(),
            "Attempt_Result":"PDF_NOT_FOUND_404",
        }]
        csvwrite(self.root/CACHED_ATTEMPTS,CACHED_ATTEMPT_FIELDS,rows)
        attempt_before=(self.root/CACHED_ATTEMPTS).read_bytes()
        session=MockSession({"data":[]})
        with self.assertRaisesRegex(OfficialAnnualPdfUnavailable,
                                    "REQUEST_LEDGER_SOURCE_IDENTITY_INVALID"):
            offline_cached_index_intake(self.root,downloads=2,
                                        now=NOW,session=session)
        self.assertEqual(session.pdf_calls,0)
        self.assertEqual(session.api_calls,0)
        self.assertEqual((self.root/CACHED_ATTEMPTS).read_bytes(),attempt_before)
        self.assertFalse((self.root/PDF_LOG).exists())
        self.untouched()

    def test_daily_supervisor_centralizes_pdf_download_through_one_bounded_step(self):
        from termux_maintenance import STEPS
        commands={key:argv for key,argv,_ in STEPS}
        self.assertIn("official_annual_pdf_source_index",commands)
        self.assertIn("official_cached_annual_pdf_archive",commands)
        self.assertEqual(commands["official_annual_pdf_source_index"][-2:],
                         ["--download","0"])
        self.assertEqual(commands["official_cached_annual_pdf_archive"][-3:],
                         ["--cached-index-only","--download","1"])
        self.assertTrue(all("order" not in " ".join(command).lower()
                            for command in commands.values()))

    def test_cached_index_cli_defaults_to_zero_http_requests(self):
        import sys
        with (
            patch.object(sys,"argv",["nse_official_annual_pdf_auto_index.py",
                                      "--cached-index-only"]),
            patch("nse_official_annual_pdf_auto_index.data_root",
                  return_value=self.root),
            patch("nse_official_annual_pdf_auto_index.requests.Session",
                  side_effect=AssertionError("NO_HTTP_IN_DEFAULT_CACHED_MODE")),
        ):
            outcome=main()
        self.assertEqual(outcome,0)
        self.assertFalse((self.root/PDF_LOG).exists())
        self.assertTrue((self.root/CACHED_STATUS).exists())
        self.assertEqual(json.loads((self.root/CACHED_STATUS).read_text())[
            "pdf_archive_requests_this_run"],0)
        self.untouched()

    def test_cached_index_cli_validation_error_preserves_api_cooldown_bytes(self):
        import sys
        tampered=dict(self.entries[0])
        tampered["ISIN"]="INE000000000"
        csvwrite(self.root/OUTPUT,OUTPUT_FIELDS,[tampered])
        status_before=(self.root/STATUS).read_bytes()
        with (
            patch.object(sys,"argv",["nse_official_annual_pdf_auto_index.py",
                                      "--cached-index-only","--download","0"]),
            patch("nse_official_annual_pdf_auto_index.data_root",
                  return_value=self.root),
            patch("nse_official_annual_pdf_auto_index.requests.Session",
                  side_effect=AssertionError("NO_HTTP_ON_BAD_CACHE")),
        ):
            outcome=main()
        self.assertEqual(outcome,1)
        self.assertEqual((self.root/STATUS).read_bytes(),status_before)
        separate=json.loads((self.root/CACHED_STATUS).read_text())
        self.assertEqual(separate["state"],"CACHED_NSE_ANNUAL_PDF_INDEX_BLOCKED")
        self.assertIn("ISSUER_ISIN",separate["reason"])
        self.assertFalse((self.root/PDF_LOG).exists())
        self.untouched()

    def test_cached_index_cli_does_not_treat_pdf_403_as_metadata_block(self):
        import sys
        from requests import HTTPError
        with (
            patch.object(sys,"argv",["nse_official_annual_pdf_auto_index.py",
                                      "--cached-index-only","--download","1"]),
            patch("nse_official_annual_pdf_auto_index.data_root",
                  return_value=self.root),
            patch("nse_official_annual_pdf_auto_index.offline_cached_index_intake",
                  side_effect=OfficialAnnualPdfUnavailable(
                      "NSE_ANNUAL_PDF_ACCESS_BLOCKED_403")),
        ):
            outcome=main()
        self.assertEqual(outcome,1)
        self.assertEqual((self.root/STATUS).read_text(),
                         json.dumps(self.blocked))
        self.assertEqual(json.loads((self.root/CACHED_STATUS).read_text())[
            "state"],"CACHED_NSE_ANNUAL_PDF_INDEX_BLOCKED")
        self.untouched()

    def test_empty_cached_pdf_index_is_read_only_no_http(self):
        (self.root/OUTPUT).unlink()
        with patch("nse_official_annual_pdf_auto_index.requests.Session",
                   side_effect=AssertionError("NO NETWORK")):
            result=offline_cached_index_intake(self.root,
                                               downloads=1,now=NOW)
        self.assertEqual(result["state"],
                         "CACHED_NSE_ANNUAL_PDF_INDEX_NO_PENDING_DOCUMENTS")
        self.assertEqual(result["previously_indexed_official_pdf_candidate_links"],0)
        self.untouched()


if __name__=="__main__":
    unittest.main()
