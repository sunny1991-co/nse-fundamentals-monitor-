"""Offline NSE annual-report metadata and bounded candidate-PDF regressions."""
from datetime import datetime,timedelta,timezone
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock

from nse_official_annual_pdf_auto_index import (
    source_pdf,parse_metadata,collect,OUTPUT,PDF_LOG,STATUS,LEDGER,
    OfficialAnnualPdfUnavailable,MAX_PDF_BYTES,choose_symbols,
    baseline_coverage,annual_source_priority,offline_pdf_source_plan,source_access_cooldown,
)
from nse_mainboard_discovery import CATALOG


def pdf_bytes():
    return b"%PDF-1.6\n"+b"Z"*4100+b"\n%%EOF"


URL="https://nsearchives.nseindia.com/annual_reports/AR_123_NEW_2022_2023_A_123.pdf"


class MockResponse:
    def __init__(self,body=None,status=200):
        self.status_code=status
        self._body=body
        self.content=b'{}'
    def json(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def iter_content(self,chunk):
        return [self._body if isinstance(self._body,bytes) else b""]


class MockSession:
    def __init__(self,body,status=200):
        self.body=body
        self.status=status
        self.api_calls=0
        self.pdf_calls=0
    def get(self,url,**kwargs):
        if "/api/annual-reports" in url:
            self.api_calls+=1
            return MockResponse(self.body,self.status)
        if "/annual_reports/" in url:
            self.pdf_calls+=1
            return MockResponse(pdf_bytes())
        raise AssertionError("unexpected URL "+url)


class OfficialAnnualAutoIndexTests(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root=Path(d.name)
        self.now=datetime(2026,10,17,10,tzinfo=timezone.utc)
        with (self.root/CATALOG).open("w",newline="") as out:
            writer=csv.DictWriter(out,fieldnames=["Symbol","ISIN","Company","Baseline_Tracked"])
            writer.writeheader()
            writer.writerow(dict(Symbol="NEW",ISIN="INE001A01011",Company="New Company",
                                 Baseline_Tracked="false"))
        self.patch=patch("nse_official_annual_pdf_auto_index.verified_discovered_symbols",
                         return_value={"NEW"})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        (self.root/"annual_historical_results.csv").write_text("STORED VERIFIED HISTORY")
        (self.root/"market_ohlcv.csv").write_text("SAVED ADJUSTED PRICES")
        (self.root/"swing_watchlist.json").write_text("SAVED STRICT WATCHLIST")
        self.good={"data":[
            {"fromYr":2022,"toYr":2023,"fileName":URL,
             "companyName":"New Company Limited",
             "broadcast_dttm":"2023-08-12 09:00:00"},
            {"fromYr":2023,"toYr":2024,"fileName":
             "https://nsearchives.nseindia.com/annual_reports/AR_123_NEW_2023_2024_A_555.pdf"},
        ]}

    def test_safe_official_pdf_links_must_name_symbol(self):
        self.assertTrue(source_pdf(URL,"NEW"))
        self.assertFalse(source_pdf("https://example.org/annual_reports/AR_123_NEW_2022_2023.pdf","NEW"))
        self.assertFalse(source_pdf("https://nsearchives.nseindia.com/annual_reports/AR_123_FAKE_2022_2023.pdf","NEW"))
        self.assertFalse(source_pdf("https://nsearchives.nseindia.com/annual_reports/AR_123_NEW_2022_2023.pdf?q=1","NEW"))

    def test_strict_response_years_and_fiscal_year(self):
        rows=parse_metadata(self.good,"NEW","INE001A01011","New Company","2026-10-17")
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["Financial_Year"],"2022-23")
        self.assertEqual(rows[1]["Financial_Year"],"2023-24")
        self.assertTrue(all(r["Verification_State"]=="OFFICIAL_PDF_CANDIDATE_UNAUDITED_NUMERIC_FACTS" for r in rows))
        with self.assertRaisesRegex(OfficialAnnualPdfUnavailable,"SCHEMA_CHANGED"):
            parse_metadata([],"NEW","INE001A01011","New Company","")

    def test_capture_metadata_and_bounded_unverified_pdf(self):
        s=MockSession(self.good)
        a=collect(self.root,limit=1,downloads=1,session=s,now=self.now)
        self.assertEqual(a["new_validated_pdf_metadata_candidates"],2)
        self.assertEqual(a["new_archived_pdf_candidates_this_run"],1)
        self.assertEqual(a["new_strict_fundamental_passes"],0)
        self.assertEqual(a["orders"],"DISABLED")
        self.assertEqual(len(list(csv.DictReader((self.root/OUTPUT).open()))),2)
        receipts=list(csv.DictReader((self.root/PDF_LOG).open()))
        self.assertEqual(len(receipts),1)
        self.assertEqual(receipts[0]["Review_Status"],
                         "REQUIRES_AUDITED_CONSOLIDATED_FULL_YEAR_NUMERIC_RECONCILIATION")
        self.assertEqual((self.root/"annual_historical_results.csv").read_text(),
                         "STORED VERIFIED HISTORY")
        self.assertEqual((self.root/"market_ohlcv.csv").read_text(),
                         "SAVED ADJUSTED PRICES")
        self.assertEqual((self.root/"swing_watchlist.json").read_text(),
                         "SAVED STRICT WATCHLIST")
        b=collect(self.root,limit=1,downloads=1,session=s,now=self.now)
        self.assertEqual(b["new_validated_pdf_metadata_candidates"],0)
        self.assertEqual(len(list(csv.DictReader((self.root/PDF_LOG).open()))),2)
        self.assertEqual(s.pdf_calls,2)

    def test_official_403_does_not_fetch_pdfs_or_approve(self):
        s=MockSession(self.good,status=403)
        r=collect(self.root,limit=1,downloads=1,session=s,now=self.now)
        self.assertEqual(r["state"],"NSE_OFFICIAL_ANNUAL_INDEX_ACCESS_BLOCKED")
        self.assertEqual(s.api_calls,1)
        self.assertEqual(s.pdf_calls,0)
        self.assertFalse((self.root/PDF_LOG).exists())
        self.assertFalse((self.root/"final_fundamental_shortlist.csv").exists())

    def test_two_complete_baseline_fys_drive_priority_not_false_trade_approval(self):
        # A complete six-field baseline prioritizes fetching ORIGINAL audited
        # annual PDF evidence; it never makes any annual numeric fact verified.
        core={
            "Revenue_Operations_Cr":"20","Net_Profit_Cr":"3",
            "Basic_EPS_Rs":"1","Total_Assets_Cr":"18",
            "Total_Equity_Cr":"15","Total_Borrowings_Cr":"0",
        }
        with (self.root/CATALOG).open("a",newline="") as out:
            writer=csv.DictWriter(out,fieldnames=[
                "Symbol","ISIN","Company","Baseline_Tracked"])
            writer.writerow(dict(Symbol="ZFAST",ISIN="INE999A01011",
                                 Company="Candidate Research",Baseline_Tracked="true"))
        for filename in ("FY2425.csv","NSE_FY2025-26_Consolidated_Fundamentals.csv"):
            with (self.root/filename).open("w",newline="") as out:
                w=csv.DictWriter(out,fieldnames=["Symbol",*core])
                w.writeheader()
                w.writerow({"Symbol":"ZFAST",**core})
                w.writerow({"Symbol":"NEW",**{**core,
                             "Net_Profit_Cr":"not_verified_number"}})
        with patch(
            "nse_official_annual_pdf_auto_index.verified_discovered_symbols",
            return_value={"NEW","ZFAST"},
        ):
            ranked=choose_symbols(self.root,self.now,2)
            self.assertEqual([r["Symbol"] for r in ranked],["ZFAST","NEW"])
            s=MockSession(self.good)
            # Synthetic per-symbol NSE annual report index payload.
            class BySymbol(MockSession):
                def get(self,url,**kwargs):
                    if "/api/annual-reports" in url:
                        sym=kwargs["params"]["symbol"]
                        self.api_calls+=1
                        return MockResponse({"data":[{
                            "fromYr":2022,"toYr":2023,
                            "fileName":"https://nsearchives.nseindia.com/annual_reports/AR_123_"
                                      +sym+"_2022_2023_A_123.pdf",
                        }]})
                    return super().get(url,**kwargs)
            provider=BySymbol(self.good)
            result=collect(self.root,limit=2,downloads=1,
                           session=provider,now=self.now)
        self.assertEqual(result["prioritized_two_year_six_core_company_sources_selected"],1)
        self.assertTrue(result["intake_priority_is_not_audited_fundamental_verification"])
        receipts=list(csv.DictReader((self.root/PDF_LOG).open()))
        self.assertEqual(len(receipts),1)
        self.assertEqual(receipts[0]["Symbol"],"ZFAST")
        self.assertEqual(result["new_strict_fundamental_passes"],0)
        self.assertFalse((self.root/"final_fundamental_shortlist.csv").exists())

        # Failed/blocked metadata sources must not starve entirely new listed
        # NSE EQ companies forever: earliest last-checked gets retried first.
        prior=[
            {"Symbol":"ZFAST","Checked_At_UTC":"2026-10-15T00:00:00+00:00",
             "Status":"OFFICIAL_METADATA_RECEIVED","Matching_PDF_Count":1},
        ]
        with (self.root/LEDGER).open("w",newline="") as out:
            w=csv.DictWriter(out,fieldnames=list(prior[0]))
            w.writeheader();w.writerows(prior)
        with patch(
            "nse_official_annual_pdf_auto_index.verified_discovered_symbols",
            return_value={"NEW","ZFAST"},
        ):
            reordered=choose_symbols(self.root,self.now,2)
        self.assertEqual([r["Symbol"] for r in reordered],["NEW","ZFAST"])

    def test_annual_pdf_plan_is_no_network_no_writes_no_false_audit(self):
        before={
            name:(self.root/name).read_bytes()
            for name in ("annual_historical_results.csv",
                         "market_ohlcv.csv","swing_watchlist.json")
        }
        with patch("nse_official_annual_pdf_auto_index.requests.Session",
                   side_effect=AssertionError("NO_HTTP_SESSION_IN_OFFLINE_PLAN")):
            result=collect(self.root,limit=1,downloads=2,now=self.now,
                           plan_only=True)
        self.assertEqual(result["state"],
                         "OFFICIAL_NSE_ANNUAL_PDF_SOURCE_PLAN_ONLY_NO_HTTP")
        self.assertEqual(result["selected_mainboard_symbols"],1)
        self.assertEqual(result["annual_api_requests"],0)
        self.assertEqual(result["pdf_download_requests"],0)
        self.assertEqual(result["new_audited_fundamental_facts"],0)
        self.assertEqual(result["orders"],"DISABLED")
        self.assertEqual(result["proposed_next_source_symbols"][0]["Symbol"],"NEW")
        self.assertEqual(result["proposed_next_source_symbols"][0]["Next_Research_Action"],
                         "CHECK_ORIGINAL_NSE_ANNUAL_REPORT_METADATA_IF_ACCESS_ALLOWED")
        self.assertFalse((self.root/STATUS).exists())
        self.assertFalse((self.root/LEDGER).exists())
        for name,content in before.items():
            self.assertEqual((self.root/name).read_bytes(),content)

    def test_offline_plan_uses_already_attested_index_link_not_imaginary_pdf(self):
        metadata=parse_metadata(self.good,"NEW","INE001A01011","New Company",
                                self.now.isoformat())
        with (self.root/OUTPUT).open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=list(metadata[0]))
            w.writeheader()
            w.writerows(metadata)
        result=offline_pdf_source_plan(self.root,self.now,1)
        self.assertEqual(result["indexed_original_nse_pdf_links_by_year"],
                         {"2022-23":1,"2023-24":1})
        self.assertEqual(result["indexed_source_links_without_receipt"],2)
        self.assertEqual(result["cached_source_receipts_not_rehashed"],0)
        self.assertEqual(result["proposed_next_source_symbols"][0]["Next_Research_Action"],
                         "DOWNLOAD_ALREADY_INDEXED_NSE_ANNUAL_PDF_IF_ACCESS_ALLOWED")
        self.assertEqual(result["annual_api_requests"],0)
        self.assertFalse((self.root/PDF_LOG).exists())

    def test_real_metadata_403_cooldown_prevents_repeated_source_calls(self):
        provider=MockSession(self.good,status=403)
        first=collect(self.root,limit=1,downloads=1,session=provider,
                      now=self.now)
        self.assertEqual(first["state"],"NSE_OFFICIAL_ANNUAL_INDEX_ACCESS_BLOCKED")
        self.assertEqual(provider.api_calls,1)
        original=(self.root/STATUS).read_bytes()
        expiry=(self.now+timedelta(hours=20)).isoformat()
        self.assertEqual(source_access_cooldown(
            self.root,self.now+__import__("datetime").timedelta(hours=2)),expiry)
        # Even explicitly re-invoked with a brand-new mock session, the
        # blocking status triggers before any NSE request or PDF I/O.
        fresh=MockSession(self.good)
        second=collect(self.root,limit=1,downloads=2,session=fresh,
                       now=self.now+__import__("datetime").timedelta(hours=2))
        self.assertEqual(second["state"],
                         "NSE_ANNUAL_SOURCE_ACCESS_COOLDOWN_NO_REQUEST")
        self.assertEqual(second["next_eligible_at_utc"],expiry)
        self.assertEqual(second["annual_api_requests"],0)
        self.assertEqual(fresh.api_calls,0)
        self.assertEqual(fresh.pdf_calls,0)
        self.assertEqual((self.root/STATUS).read_bytes(),original)
        self.assertEqual(offline_pdf_source_plan(
            self.root,self.now+__import__("datetime").timedelta(hours=2),1
        )["previous_access_cooldown_until_utc"],expiry)

    def test_resume_after_original_source_cooldown_has_expired(self):
        denied=MockSession(self.good,status=403)
        collect(self.root,limit=1,downloads=0,session=denied,now=self.now)
        approved=MockSession(self.good)
        after=collect(self.root,limit=1,downloads=0,session=approved,
                      now=self.now+__import__("datetime").timedelta(hours=21))
        self.assertEqual(after["state"],"OFFICIAL_ANNUAL_REPORT_METADATA_CAPTURED")
        self.assertEqual(approved.api_calls,1)
        self.assertEqual(after["new_strict_fundamental_passes"],0)
        self.assertEqual(after["orders"],"DISABLED")

    def test_landing_access_denial_timestamp_is_respected(self):
        blocked={
            "state":"OFFICIAL_ANNUAL_INDEX_BLOCKED",
            "reason":"NSE_ANNUAL_LANDING_ACCESS_BLOCKED",
            "checked_at_utc":self.now.isoformat(),
        }
        (self.root/STATUS).parent.mkdir(parents=True,exist_ok=True)
        (self.root/STATUS).write_text(json.dumps(blocked))
        fresh=MockSession(self.good)
        out=collect(self.root,limit=1,downloads=0,session=fresh,
                    now=self.now+__import__("datetime").timedelta(hours=5))
        self.assertEqual(out["state"],
                         "NSE_ANNUAL_SOURCE_ACCESS_COOLDOWN_NO_REQUEST")
        self.assertEqual(fresh.api_calls,0)
        self.assertEqual(fresh.pdf_calls,0)

    def test_invalid_or_too_large_document_must_not_create_receipt(self):
        s=MockSession(self.good)
        with patch("nse_official_annual_pdf_auto_index.MAX_PDF_BYTES",100):
            r=collect(self.root,limit=1,downloads=1,session=s,now=self.now)
        self.assertEqual(r["new_archived_pdf_candidates_this_run"],0)
        self.assertEqual(len(list(csv.DictReader((self.root/PDF_LOG).open()))),0)


if __name__=="__main__":
    unittest.main()
