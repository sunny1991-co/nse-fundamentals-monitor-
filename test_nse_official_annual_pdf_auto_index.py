"""Offline NSE annual-report metadata and bounded candidate-PDF regressions."""
from datetime import datetime,timezone
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock

from nse_official_annual_pdf_auto_index import (
    source_pdf,parse_metadata,collect,OUTPUT,PDF_LOG,STATUS,LEDGER,
    OfficialAnnualPdfUnavailable,MAX_PDF_BYTES,
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

    def test_invalid_or_too_large_document_must_not_create_receipt(self):
        s=MockSession(self.good)
        with patch("nse_official_annual_pdf_auto_index.MAX_PDF_BYTES",100):
            r=collect(self.root,limit=1,downloads=1,session=s,now=self.now)
        self.assertEqual(r["new_archived_pdf_candidates_this_run"],0)
        self.assertEqual(len(list(csv.DictReader((self.root/PDF_LOG).open()))),0)


if __name__=="__main__":
    unittest.main()
