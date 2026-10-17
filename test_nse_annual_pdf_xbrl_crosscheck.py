"""Offline regression suite: NSE annual PDF vs source-verified NSE XBRL diagnostics."""
from __future__ import annotations
import csv
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nse_annual_pdf_xbrl_crosscheck import (
    run, OUTPUT, STATUS, VERSION, choose_original, comparison,
    original_source_ok, CrosscheckBlocked, METRICS,
)
from nse_annual_pdf_numeric_review import (
    OUTPUT as NUMERIC, ROW_FIELDS, VERSION as NUMERIC_VERSION,
)
from nse_annual_pdf_section_review import (
    LATEST_OUTPUT, FIELDS as SECTION_FIELDS, VERSION as SECTION_VERSION,
)
from nse_official_annual_pdf_auto_index import PDF_DIR

NOW=datetime(2026,10,17,14,tzinfo=timezone.utc)
URL="https://nsearchives.nseindia.com/annual_reports/AR_24150_20MICRONS_2023_2024_20062024104857.pdf"
XBRL="https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_TEST_15052024120000_WEB.xml"
PDF=b"%PDF-1.6\n"+b"T"*5100+b"\n%%EOF"
SHA=hashlib.sha256(PDF).hexdigest()
SYMBOL="20MICRONS"
ISIN="INE144J01027"
AMOUNTS={
    "Revenue_Operations_Cr":"777.4929",
    "Net_Profit_Cr":"56.1603",
    "Basic_EPS_Rs":"15.89",
    "Total_Assets_Cr":"589.7979",
    "Total_Equity_Cr":"352.9537",
    "Total_Borrowings_Cr":"115.6203",
}


def write_csv(path,fields,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def source_row(**overrides):
    return dict({
        "Symbol":SYMBOL,"Financial_Year":"2023-24",
        "Fiscal_Year_End":"2024-03-31",
        "Reporting_Basis":"Consolidated",
        "Audit_Status":"Audited",
        "NSE_Consolidation_Verified":"true",
        "Status":"VERIFIED",
        "Source_URL":XBRL,"Filing_Published_At":"2024-05-15",
        **AMOUNTS,
    },**overrides)


class PDFXBRLCrosscheckTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.pdf=self.root/PDF_DIR/"immutable.pdf"
        self.pdf.parent.mkdir(parents=True,exist_ok=True)
        self.pdf.write_bytes(PDF)
        self.receipt={
            "Symbol":SYMBOL,"Financial_Year":"2023-24",
            "Official_Report_URL":URL,"PDF_SHA256":SHA,
        }
        self.entry={"ISIN":ISIN}
        self.pdf_row={
            "Symbol":SYMBOL,"ISIN":ISIN,"Financial_Year":"2023-24",
            "Fiscal_Year_End":"2024-03-31",
            "Official_Report_URL":URL,"PDF_SHA256":SHA,
            "Numeric_Review_Version":NUMERIC_VERSION,
            "Section_Review_Version":SECTION_VERSION,
            "Review_State":"COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED",
            "Candidate_Core_Fields":"6",
            "Balance_Sheet_Check":"SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY",
            "Profit_Tax_Check":"SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY",
            "Borrowings_Check":"BOTH_ROW_CANDIDATES_FOUND_UNVERIFIED",
            "Audited_Consolidated_Facts_Verified":"false",
            "Strict_Fundamental_Pass":"false","Orders":"DISABLED",
            **AMOUNTS,
        }
        self.section={
            "Symbol":SYMBOL,"Financial_Year":"2023-24",
            "Official_Report_URL":URL,"PDF_SHA256":SHA,
            "Review_Version":SECTION_VERSION,
            "Section_Review_State":"CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED",
        }
        self.write_input()
        self.set_xbrl([source_row()])
        self.identity=patch(
            "nse_annual_pdf_xbrl_crosscheck.checked_sources",
            return_value=[(self.receipt,self.entry,self.pdf)],
        )
        self.identity.start()
        self.addCleanup(self.identity.stop)
        self.protected={
            "annual_historical_results.csv":None,
            "final_fundamental_shortlist.csv":"DO_NOT_CHANGE",
            "swing_watchlist.json":"DO_NOT_CHANGE",
        }
        for name,contents in self.protected.items():
            if contents is not None:
                (self.root/name).write_text(contents)
        self.original_bytes=(self.root/"annual_historical_results.csv").read_bytes()

    def write_input(self):
        write_csv(self.root/NUMERIC,ROW_FIELDS,[self.pdf_row])
        write_csv(self.root/LATEST_OUTPUT,SECTION_FIELDS,[self.section])

    def set_xbrl(self,rows):
        headers=[
            "Symbol","Financial_Year","Fiscal_Year_End",
            "Reporting_Basis","Audit_Status","NSE_Consolidation_Verified",
            "Status","Source_URL","Filing_Published_At",*METRICS,
        ]
        write_csv(self.root/"annual_historical_results.csv",headers,rows)

    def assert_protected(self):
        self.assertEqual((self.root/"final_fundamental_shortlist.csv").read_text(),"DO_NOT_CHANGE")
        self.assertEqual((self.root/"swing_watchlist.json").read_text(),"DO_NOT_CHANGE")

    def report_row(self):
        with (self.root/OUTPUT).open(newline="") as f:
            return next(csv.DictReader(f))

    def test_exact_six_number_matches_never_upgrade_pdf_or_add_strict_pass(self):
        summary=run(self.root,now=NOW,max_files=1)
        self.assertEqual(summary["reports_checked"],1)
        self.assertEqual(summary["source_verified_original_xbrl_years_compared"],1)
        self.assertEqual(summary["strict_four_year_passes_added"],0)
        self.assertEqual(summary["orders"],"DISABLED")
        r=self.report_row()
        self.assertEqual(r["Review_State"],"ALL_SIX_NUMERIC_VALUES_CLOSE_SOURCE_REVIEW_ONLY")
        self.assertEqual(r["Numeric_Matched"],"6")
        self.assertEqual(r["Numeric_Discrepant"],"0")
        self.assertEqual(r["Official_XBRL_URL"],XBRL)
        self.assertEqual(r["PDF_Review_Version"],NUMERIC_VERSION)
        self.assertEqual(r["Audited_Facts_Promoted"],"0")
        self.assertEqual(r["Strict_Annual_Passes_Added"],"0")
        comparison_rows=json.loads(r["Metric_Results_JSON"])
        self.assertEqual(comparison_rows["Basic_EPS_Rs"]["state"],
                         "NUMERIC_WITHIN_TOLERANCE_CANDIDATE_ONLY")
        self.assertEqual((self.root/"annual_historical_results.csv").read_bytes(),
                         self.original_bytes)
        self.assert_protected()

    def test_no_original_xbrl_file_reports_gap_no_fake_verified(self):
        (self.root/"annual_historical_results.csv").unlink()
        result=run(self.root,now=NOW,max_files=1)
        self.assertEqual(result["missing_original_xbrl_years"],1)
        self.assertEqual(result["source_verified_original_xbrl_years_compared"],0)
        self.assertEqual(self.report_row()["Review_State"],
                         "ORIGINAL_NSE_XBRL_HISTORY_NOT_AVAILABLE")
        self.assertEqual(self.report_row()["Official_XBRL_URL"],"")
        self.assert_protected()

    def test_unverified_or_vendor_style_xbrl_does_not_count(self):
        self.set_xbrl([source_row(NSE_Consolidation_Verified="false")])
        result=run(self.root,now=NOW,max_files=1)
        self.assertEqual(result["missing_original_xbrl_years"],1)
        self.assertEqual(self.report_row()["Review_State"],
                         "ORIGINAL_NSE_XBRL_HISTORY_NOT_SOURCE_VERIFIED")
        self.assert_protected()

    def test_source_mismatch_review_only_and_never_overwrite_baseline(self):
        self.set_xbrl([source_row(Revenue_Operations_Cr="700.00")])
        original=(self.root/"annual_historical_results.csv").read_bytes()
        result=run(self.root,now=NOW,max_files=1)
        self.assertEqual(result["reports_with_accounting_differences"],1)
        row=self.report_row()
        self.assertEqual(row["Review_State"],"PDF_VS_XBRL_DIFFERENCE_REVIEW_REQUIRED")
        self.assertEqual(row["Numeric_Discrepant"],"1")
        self.assertEqual((self.root/"annual_historical_results.csv").read_bytes(),original)
        self.assert_protected()

    def test_same_day_two_different_xbrl_urls_block_comparison(self):
        another=XBRL.replace("TEST_","OTHER_")
        self.set_xbrl([source_row(),source_row(Source_URL=another)])
        result=run(self.root,now=NOW,max_files=1)
        self.assertEqual(result["ambiguous_original_xbrl_revision_years"],1)
        self.assertEqual(self.report_row()["Review_State"],
                         "AMBIGUOUS_XBRL_REVISIONS_OR_CONFLICTING_NUMERIC_FACTS")
        self.assert_protected()

    def test_obsolete_pdf_numeric_version_cannot_be_crosschecked(self):
        self.pdf_row["Numeric_Review_Version"]="numeric-evidence-candidate-v1"
        self.write_input()
        report=run(self.root,now=NOW,max_files=1)
        self.assertEqual(report["reports_checked"],0)
        self.assertEqual(report["complete_pdf_candidate_rows"],0)
        self.assert_protected()

    def test_original_pdf_checksum_changed_blocks_and_preserves_xbrl(self):
        self.pdf.write_bytes(PDF+b"changed")
        with self.assertRaisesRegex(CrosscheckBlocked,"SOURCE_BYTES_CHANGED"):
            run(self.root,now=NOW,max_files=1)
        self.assertFalse((self.root/OUTPUT).exists())
        self.assert_protected()

    def test_mismatched_source_isin_fails_closed(self):
        self.pdf_row["ISIN"]="INE999A01011"
        self.write_input()
        with self.assertRaisesRegex(CrosscheckBlocked,"PDF_ISIN"):
            run(self.root,now=NOW,max_files=1)
        self.assert_protected()

    def test_pdf_candidate_missing_current_section_is_rejected(self):
        self.section["Review_Version"]="section-review-2-adaptive"
        self.write_input()
        with self.assertRaisesRegex(CrosscheckBlocked,"ATTESTATION_MISSING"):
            run(self.root,now=NOW,max_files=1)
        self.assert_protected()

    def test_rounded_eps_candidate_within_tolerance_not_verified(self):
        self.set_xbrl([source_row(Basic_EPS_Rs="15.90")])
        report=run(self.root,now=NOW,max_files=1)
        self.assertEqual(report["reports_with_accounting_differences"],0)
        self.assertEqual(self.report_row()["Numeric_Matched"],"6")
        self.assert_protected()

    def test_source_metadata_and_schema_are_mandatory(self):
        self.assertFalse(original_source_ok(source_row(
            Source_URL="https://evil.invalid/corporate/xbrl/forged.xml")))
        self.assertFalse(original_source_ok(source_row(Reporting_Basis="Standalone")))
        self.assertFalse(original_source_ok(source_row(Audit_Status="Unaudited")))
        self.assertFalse(original_source_ok(source_row(Fiscal_Year_End="2023-03-31")))
        with self.assertRaisesRegex(ValueError,"MAX_FILES"):
            run(self.root,max_files=3)
        self.assert_protected()


if __name__=="__main__":
    unittest.main()
