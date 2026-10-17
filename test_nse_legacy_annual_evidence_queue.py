"""Offline combined NSE mainboard source coverage queue regression tests."""
import csv
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nse_legacy_annual_evidence_queue import (
    snapshot, OUTPUT, STATUS, FIELDS, CoverageBlocked, api_cooldown,
)
from nse_mainboard_discovery import CATALOG
from nse_official_annual_pdf_auto_index import OUTPUT as PDF_INDEX,OUTPUT_FIELDS
from nse_annual_pdf_section_review import LATEST_OUTPUT,FIELDS as SECTION_FIELDS,VERSION as SECTION_VERSION
from nse_annual_pdf_numeric_review import OUTPUT as NUMERIC_OUTPUT,ROW_FIELDS,VERSION as NUMERIC_VERSION
from nse_annual_pdf_prior_year_comparatives import OUTPUT as PRIOR_OUTPUT,FIELDS as PRIOR_FIELDS,VERSION as PRIOR_VERSION
from nse_legacy_annual_official_auto_index import LEDGER

# Keeping these tests self-contained, no HTTP downloads, NSE sessions,
# broker connections, CSV baseline mutations or traded watchlist edits.
NOW=datetime(2026,10,17,11,20,tzinfo=timezone.utc)
URL="https://nsearchives.nseindia.com/annual_reports/AR_24150_20MICRONS_2023_2024_20062024104857.pdf"
SHA="a"*64
SYM="20MICRONS"
ISIN="INE144J01027"
SECOND="SECOND"
SECOND_ISIN="INE123A01011"
THIRD="THIRD"
THIRD_ISIN="INE999A01011"
XBRL="https://nsearchives.nseindia.com/corporate/xbrl/INDAS_TEST_20062024104857.xml"
CORE={
    "Revenue_Operations_Cr":"777.4929","Net_Profit_Cr":"56.1603",
    "Basic_EPS_Rs":"15.89","Total_Assets_Cr":"589.7979",
    "Total_Equity_Cr":"352.9537","Total_Borrowings_Cr":"115.6203",
}


def save(path,fields,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


class LegacyEvidenceQueueTests(unittest.TestCase):
    def setUp(self):
        t=TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.catalog=[
            {"Symbol":SYM,"ISIN":ISIN,"Company":"20 Microns Limited",
             "Series":"EQ","Baseline_Tracked":"true"},
            {"Symbol":SECOND,"ISIN":SECOND_ISIN,"Company":"Other NSE Company",
             "Series":"EQ","Baseline_Tracked":"true"},
            {"Symbol":THIRD,"ISIN":THIRD_ISIN,"Company":"Third NSE Company",
             "Series":"EQ","Baseline_Tracked":"false"},
        ]
        save(self.root/CATALOG,list(self.catalog[0]),self.catalog)
        self.verified=patch(
            "nse_legacy_annual_evidence_queue.verified_discovered_symbols",
            return_value={SYM,SECOND,THIRD},
        )
        self.verified.start()
        self.addCleanup(self.verified.stop)
        self.receipt={
            "Symbol":SYM,"Financial_Year":"2023-24",
            "Official_Report_URL":URL,"PDF_SHA256":SHA,
        }
        self.entry={"ISIN":ISIN}
        self.pdf=self.root/"runtime"/"archived.pdf"
        self.pdf.parent.mkdir(parents=True,exist_ok=True)
        self.pdf.write_text("ORIGINAL_ARCHIVE_PRIVATE_RESEARCH_ONLY")
        self.source=patch(
            "nse_legacy_annual_evidence_queue.checked_sources",
            return_value=[(self.receipt,self.entry,self.pdf)],
        )
        self.source.start()
        self.addCleanup(self.source.stop)
        save(self.root/PDF_INDEX,OUTPUT_FIELDS,[{
            "Symbol":SYM,"ISIN":ISIN,"Financial_Year":"2023-24",
            "Official_Report_URL":URL,"Original_Metadata_SHA256":"b"*64,
        }])
        save(self.root/LATEST_OUTPUT,SECTION_FIELDS,[{
            **self.receipt,"ISIN":ISIN,"Review_Version":SECTION_VERSION,
            "Section_Review_State":"CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED",
        }])
        self.numeric={
            **self.receipt,"ISIN":ISIN,
            "Numeric_Review_Version":NUMERIC_VERSION,
            "Section_Review_Version":SECTION_VERSION,
            "Fiscal_Year_End":"2024-03-31",
            "Review_State":"COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED",
            "Candidate_Core_Fields":"6",
            "Balance_Sheet_Check":"SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY",
            "Profit_Tax_Check":"SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY",
            "Borrowings_Check":"BOTH_ROW_CANDIDATES_FOUND_UNVERIFIED",
            "Audited_Consolidated_Facts_Verified":"false",
            "Strict_Fundamental_Pass":"false","Orders":"DISABLED",**CORE,
        }
        save(self.root/NUMERIC_OUTPUT,ROW_FIELDS,[self.numeric])
        self.prior={
            "Symbol":SYM,"ISIN":ISIN,
            "Source_Report_Financial_Year":"2023-24",
            "Comparative_Financial_Year":"2022-23",
            "Comparative_Fiscal_Year_End":"2023-03-31",
            "Official_NSE_PDF_URL":URL,"Official_NSE_PDF_SHA256":SHA,
            "Comparative_Review_Version":PRIOR_VERSION,
            "Review_State":"ALL_SIX_PRIOR_YEAR_COMPARATIVE_SOURCE_CANDIDATES_NOT_AUDIT_VERIFIED",
            "Candidate_Core_Fields":"6",
            "Separately_Audited_Prior_Year_XBRL_Verified":"false",
            "Strict_Fundamental_Pass":"false",
            "Audited_Facts_Promoted":0,"Orders":"DISABLED",**CORE,
        }
        save(self.root/PRIOR_OUTPUT,PRIOR_FIELDS,[self.prior])
        save(self.root/"nse_legacy_financial_results_2023-24.csv",
             ["Symbol","Period Ended","Audited / Unaudited",
              "Consolidated / Non-Consolidated","Period","XBRL URL"],[{
                  "Symbol":SECOND,"Period Ended":"2024-03-31",
                  "Audited / Unaudited":"Audited",
                  "Consolidated / Non-Consolidated":"Consolidated",
                  "Period":"Annual","XBRL URL":XBRL,
              }])
        self.history={
            "Symbol":THIRD,"Financial_Year":"2022-23",
            "Fiscal_Year_End":"2023-03-31",
            "Reporting_Basis":"Consolidated","Audit_Status":"Audited",
            "NSE_Consolidation_Verified":"true",
            "Status":"VERIFIED","Source_URL":XBRL,
            "Filing_Published_At":"2023-06-20",**CORE,
        }
        save(self.root/"annual_historical_results.csv",list(self.history),[self.history])
        self.ledger={
            "Symbol":SYM,
            "Checked_At_UTC":"2026-10-17T10:44:22.777832+00:00",
            "State":"INDEX_DISCOVERY_BLOCKED","Rows_Indexed":"0",
            "Reason":"OFFICIAL_NSE_OLD_RESULTS_READ_TIMEOUT",
            "Diagnostic_Version":"legacy-nse-response-diagnostic-v2",
            "Target_Financial_Year":"2023-24",
        }
        save(self.root/LEDGER,list(self.ledger),[self.ledger])
        self.protected={
            "annual_historical_results.csv":
                (self.root/"annual_historical_results.csv").read_bytes(),
            "final_fundamental_shortlist.csv":b"STRICT_SCREEN_NOT_TOUCHED",
            "swing_watchlist.json":b"LIVE_ORDERS_DISABLED",
        }
        for filename,contents in self.protected.items():
            (self.root/filename).write_bytes(contents)

    def rows(self):
        with (self.root/OUTPUT).open(newline="") as f:
            return {(r["Symbol"],r["Financial_Year"]):r for r in csv.DictReader(f)}

    def assert_protected(self):
        for filename,original in self.protected.items():
            self.assertEqual((self.root/filename).read_bytes(),original)

    def test_two_fy_every_official_eq_company_with_stages_and_no_promotions(self):
        state=snapshot(self.root,now=NOW)
        self.assertEqual(state["official_mainboard_eq_companies"],3)
        self.assertEqual(state["baseline_tracked_current_mainboard_companies"],2)
        self.assertEqual(state["company_years_in_queue"],6)
        self.assertEqual(state["old_nse_api_calls_made"],0)
        self.assertEqual(state["original_audited_xbrl_filings_reverified_this_run"],0)
        self.assertEqual(state["strict_fundamental_passes_added"],0)
        self.assertEqual(state["broker_orders"],"DISABLED")
        rows=self.rows()
        self.assertEqual(rows[(SYM,"2023-24")]["Source_Stage"],
                         "PDF_SIX_CORE_NUMERIC_CANDIDATES_ONLY")
        self.assertEqual(rows[(SYM,"2022-23")]["Source_Stage"],
                         "LATER_ANNUAL_PDF_PRIOR_FY_COMPARATIVE_ONLY")
        self.assertEqual(rows[(SYM,"2022-23")][
            "Previous_FY_Comparative_Six_Core_From_Later_PDF"],"true")
        self.assertEqual(rows[(SYM,"2022-23")]["Original_XBRL_Six_Core_Fields_In_Local_History"],"false")
        self.assertEqual(rows[(SECOND,"2023-24")]["Source_Stage"],
                         "ORIGINAL_XBRL_INDEX_LINKS_ONLY")
        self.assertEqual(rows[(THIRD,"2022-23")]["Source_Stage"],
                         "LOCAL_ORIGINAL_XBRL_SIX_CORE_ROW")
        self.assertEqual(rows[(THIRD,"2023-24")]["Source_Stage"],
                         "NO_LOCAL_LEGACY_SOURCE_INDEX_YET")
        self.assertEqual(rows[(SYM,"2023-24")][
            "Old_NSE_Source_API_Next_Eligible_UTC"],
            "2026-10-18T06:44:22.777832+00:00")
        self.assertTrue(all(r["Trade_Eligible"]=="false" for r in rows.values()))
        self.assertTrue(all(r["Orders"]=="DISABLED" for r in rows.values()))
        self.assertTrue(all(r["Original_Audited_XBRL_Reverified_This_Run"]=="false" for r in rows.values()))
        self.assert_protected()

    def test_prior_year_column_never_claimed_as_independent_xbrl(self):
        self.prior["Separately_Audited_Prior_Year_XBRL_Verified"]="true"
        save(self.root/PRIOR_OUTPUT,PRIOR_FIELDS,[self.prior])
        snapshot(self.root,now=NOW)
        previous=self.rows()[(SYM,"2022-23")]
        self.assertEqual(previous["Previous_FY_Comparative_Six_Core_From_Later_PDF"],"false")
        self.assertEqual(previous["Source_Stage"],"NO_LOCAL_LEGACY_SOURCE_INDEX_YET")
        self.assert_protected()

    def test_wrong_isin_pdf_candidate_never_counted(self):
        self.numeric["ISIN"]=SECOND_ISIN
        save(self.root/NUMERIC_OUTPUT,ROW_FIELDS,[self.numeric])
        snapshot(self.root,now=NOW)
        rec=self.rows()[(SYM,"2023-24")]
        self.assertEqual(rec["PDF_Six_Core_Candidates_Not_XBRL"],"false")
        self.assertEqual(rec["Source_Stage"],"PDF_CONSOLIDATED_STATEMENT_PAGES_CANDIDATE")
        self.assert_protected()

    def test_wrong_official_catalog_count_does_not_rewrite_previous_queue(self):
        snapshot(self.root,now=NOW)
        before=(self.root/OUTPUT).read_bytes()
        save(self.root/CATALOG,list(self.catalog[0]),self.catalog[:2])
        with self.assertRaisesRegex(CoverageBlocked,"CATALOG_NOT_ATTESTED"):
            snapshot(self.root,now=NOW)
        self.assertEqual((self.root/OUTPUT).read_bytes(),before)
        self.assert_protected()

    def test_original_xbrl_revision_conflict_not_ranked_as_verified(self):
        other={**self.history,"Source_URL":XBRL.replace("TEST","REVISED")}
        save(self.root/"annual_historical_results.csv",
             list(self.history),[self.history,other])
        snapshot(self.root,now=NOW)
        self.assertEqual(self.rows()[(THIRD,"2022-23")]["Source_Stage"],
                         "ORIGINAL_XBRL_CONFLICTING_REVISIONS")
        self.assert_protected_except_history()

    def assert_protected_except_history(self):
        for filename in ("final_fundamental_shortlist.csv","swing_watchlist.json"):
            self.assertEqual((self.root/filename).read_bytes(),self.protected[filename])

    def test_no_local_pdf_sources_does_not_claim_facts(self):
        self.source.stop()
        alternative=patch("nse_legacy_annual_evidence_queue.checked_sources",
                          return_value=[])
        alternative.start()
        self.addCleanup(alternative.stop)
        snapshot(self.root,now=NOW)
        data=self.rows()
        self.assertEqual(data[(SYM,"2023-24")]["Source_Stage"],
                         "OFFICIAL_PDF_INDEX_LINKS_ONLY")
        self.assertEqual(data[(SYM,"2022-23")]["Source_Stage"],
                         "NO_LOCAL_LEGACY_SOURCE_INDEX_YET")
        self.assert_protected()


if __name__=="__main__":
    unittest.main()
