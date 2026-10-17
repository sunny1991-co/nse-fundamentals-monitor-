"""Fail-closed archived NSE FY22/23 vs FY23/24 PDF research YoY regressions."""
from __future__ import annotations

import csv
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nse_annual_pdf_numeric_review import (
    financial_page_evidence, OUTPUT as CURRENT_OUTPUT,
    ROW_FIELDS as CURRENT_FIELDS, VERSION as CURRENT_VERSION,
)
from nse_annual_pdf_prior_year_comparatives import (
    comparative_evidence, OUTPUT as PRIOR_OUTPUT,
    FIELDS as PRIOR_FIELDS, VERSION as PRIOR_VERSION,
)
from nse_annual_pdf_two_year_research import (
    run, comparable_percent, OUTPUT, VERSION, PDFComparisonBlocked, FIELDS,
)
from nse_annual_pdf_section_review import (
    LATEST_OUTPUT, FIELDS as SECTION_FIELDS, VERSION as SECTION_VERSION,
    SectionReviewBlocked,
)
from nse_official_annual_pdf_auto_index import PDF_DIR
from test_nse_annual_pdf_numeric_review import PDF, Reader, WrappedPDFReader, BadBalance

NOW=datetime(2026,10,17,18,tzinfo=timezone.utc)
SYM="20MICRONS"
ISIN="INE144J01027"
URL="https://nsearchives.nseindia.com/annual_reports/AR_24150_20MICRONS_2023_2024_20062024104857.pdf"
SHA=hashlib.sha256(PDF).hexdigest()


def write_rows(path,fields,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class PDFTwoYearResearchTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.pdf=self.root/PDF_DIR/"official_source.pdf"
        self.pdf.parent.mkdir(parents=True,exist_ok=True)
        self.pdf.write_bytes(PDF)
        self.receipt={
            "Symbol":SYM,"Financial_Year":"2023-24",
            "Official_Report_URL":URL,"PDF_SHA256":SHA,
        }
        self.entry={"ISIN":ISIN}
        self.review={
            **self.receipt,
            "Review_Version":SECTION_VERSION,
            "Section_Review_State":"CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED",
            "Auditor_Page_1based":"201",
            "Consolidated_Balance_Sheet_Page_1based":"209",
            "Consolidated_Profit_Loss_Page_1based":"210",
            "Consolidated_Cash_Flow_Page_1based":"211",
        }
        details=financial_page_evidence(PDF,self.review,reader_cls=Reader)
        self.assertEqual(details["state"],
                         "COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED")
        self.current={
            **self.receipt,"ISIN":ISIN,"Fiscal_Year_End":"2024-03-31",
            "Numeric_Review_Version":CURRENT_VERSION,
            "Section_Review_Version":SECTION_VERSION,
            "Review_State":details["state"],"Candidate_Core_Fields":"6",
            "Statement_Units":"INR_LAKHS",
            "Balance_Sheet_Check":"SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY",
            "Profit_Tax_Check":"SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY",
            "Borrowings_Check":"BOTH_ROW_CANDIDATES_FOUND_UNVERIFIED",
            "Audited_Consolidated_Facts_Verified":"false",
            "Strict_Fundamental_Pass":"false","Orders":"DISABLED",
            **details["metrics"],
        }
        prior_data=comparative_evidence(PDF,self.review,self.current,reader_cls=Reader)
        self.assertEqual(prior_data["state"],
                         "ALL_SIX_PRIOR_YEAR_COMPARATIVE_SOURCE_CANDIDATES_NOT_AUDIT_VERIFIED")
        self.prior={
            "Symbol":SYM,"ISIN":ISIN,"Source_Report_Financial_Year":"2023-24",
            "Comparative_Financial_Year":"2022-23",
            "Comparative_Fiscal_Year_End":"2023-03-31",
            "Official_NSE_PDF_URL":URL,"Official_NSE_PDF_SHA256":SHA,
            "Source_PDF_Numeric_Review_Version":CURRENT_VERSION,
            "Comparative_Review_Version":PRIOR_VERSION,
            "Comparative_Evidence_Type":"PRIOR_YEAR_COLUMN_OF_LATER_ANNUAL_REPORT_ONLY",
            "Unit":"INR_LAKHS_EXCEPT_EPS_RUPEES",
            "Review_State":prior_data["state"],
            "Candidate_Core_Fields":"6",
            "Comparative_Balance_Sheet_Check":"SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY",
            "Comparative_Profit_Tax_Check":"SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY",
            "Comparative_Borrowings_Check":"BOTH_PREVIOUS_YEAR_ROWS_FOUND_UNVERIFIED",
            "Separately_Audited_Prior_Year_XBRL_Verified":"false",
            "Strict_Fundamental_Pass":"false","Audited_Facts_Promoted":0,
            "Orders":"DISABLED",**prior_data["candidate"],
        }
        self.prepare()
        self.source=patch(
            "nse_annual_pdf_two_year_research.checked_sources",
            return_value=[(self.receipt,self.entry,self.pdf)])
        self.source.start()
        self.addCleanup(self.source.stop)
        self.protected={
            "annual_historical_results.csv":"NSE_AUDITED_XBRL_DO_NOT_TOUCH",
            "final_fundamental_shortlist.csv":"STRICT_SCREEN_DO_NOT_TOUCH",
            "swing_watchlist.json":"TRADE_DISABLED_DO_NOT_TOUCH",
        }
        for name,data in self.protected.items():
            (self.root/name).write_text(data)

    def prepare(self):
        write_rows(self.root/LATEST_OUTPUT,SECTION_FIELDS,[self.review])
        write_rows(self.root/CURRENT_OUTPUT,CURRENT_FIELDS,[self.current])
        write_rows(self.root/PRIOR_OUTPUT,PRIOR_FIELDS,[self.prior])

    def untouched(self):
        for name,data in self.protected.items():
            self.assertEqual((self.root/name).read_text(),data)

    def read_result(self):
        with (self.root/OUTPUT).open(newline="") as file:
            return list(csv.DictReader(file))

    def test_source_reopened_six_fields_yoy_only_no_audited_promotion(self):
        status=run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.assertEqual(status["new_two_year_comparisons"],1)
        self.assertEqual(status["total_two_year_comparisons"],1)
        self.assertEqual(status["original_nse_xbrl_facts_promoted"],0)
        self.assertEqual(status["strict_fundamental_passes_added"],0)
        self.assertEqual(status["source_api_calls"],0)
        self.assertEqual(status["orders"],"DISABLED")
        result=self.read_result()[0]
        self.assertEqual(result["State"],"TWO_YEAR_PDF_COMPARATIVE_RESEARCH_ONLY")
        self.assertEqual(result["Investment_Signal"],"NONE_RESEARCH_ONLY")
        self.assertEqual(result["Source_Audited_Annual_XBRL_Passes_Added"],"0")
        self.assertEqual(result["Current_Column_Audit_Status"],
                         "PDF_ROW_CANDIDATE_NOT_ORIGINAL_XBRL_VERIFIED")
        self.assertEqual(result["Prior_Column_Audit_Status"],
                         "LATER_REPORT_COMPARATIVE_NOT_ORIGINAL_XBRL_VERIFIED")
        expected={
            "Revenue_Operations_Cr":("701.6872","777.4929"),
            "Net_Profit_Cr":("41.9617","56.1603"),
            "Basic_EPS_Rs":("11.85","15.89"),
            "Total_Assets_Cr":("523.9198","589.7979"),
            "Total_Equity_Cr":("293.5786","352.9537"),
            "Total_Borrowings_Cr":("101.9190","115.6203"),
        }
        for field,(older,now) in expected.items():
            self.assertEqual(result[f"Prior_{field}"],older)
            self.assertEqual(result[f"Current_{field}"],now)
            self.assertEqual(result[f"Absolute_Change_{field}"],
                             str(Decimal(now)-Decimal(older)))
            expected_growth=(
                (Decimal(now)-Decimal(older))/Decimal(older)*Decimal("100")
            ).quantize(Decimal("0.01"))
            self.assertEqual(Decimal(result[f"YoY_Percent_{field}"]),
                             expected_growth)
            self.assertEqual(result[f"YoY_Interpretation_{field}"],
                             "POSITIVE_BASE_VALID")
        self.assertEqual(result["Comparison_Field_Count"],"6")
        self.assertEqual(result["Orders"],"DISABLED")
        self.assertEqual(result["Original_NSE_PDF_SHA256"],SHA)
        self.assertEqual(result["Original_NSE_PDF_URL"],URL)
        self.assertEqual(run(self.root,now=NOW,max_files=1,reader_cls=Reader)[
            "new_two_year_comparisons"],0)
        self.assertEqual(len(self.read_result()),1)
        self.untouched()

    def test_pdf_wrapped_source_rows_still_verified_from_original(self):
        outcome=run(self.root,now=NOW,max_files=1,reader_cls=WrappedPDFReader)
        self.assertEqual(outcome["new_two_year_comparisons"],1)
        self.assertEqual(self.read_result()[0]["Prior_Revenue_Operations_Cr"],
                         "701.6872")
        self.untouched()

    def test_prior_candidate_tamper_blocks_yoy_report(self):
        self.prior["Net_Profit_Cr"]="999.0000"
        self.prepare()
        with self.assertRaisesRegex(PDFComparisonBlocked,
                                     "CACHED_VALUE_CONFLICTS_WITH_ORIGINAL_NSE_PDF"):
            run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.assertFalse((self.root/OUTPUT).exists())
        self.untouched()

    def test_current_candidate_tamper_blocks_research(self):
        self.current["Revenue_Operations_Cr"]="999.0000"
        self.prepare()
        with self.assertRaisesRegex(SectionReviewBlocked,"CACHED_CURRENT"):
            run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.assertFalse((self.root/OUTPUT).exists())
        self.untouched()

    def test_pdf_replacement_sha_detected_fail_closed(self):
        self.pdf.write_bytes(PDF+b"modified")
        with self.assertRaisesRegex(PDFComparisonBlocked,"SOURCE_PDF_CONTENT_SHA256_CHANGED"):
            run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.assertFalse((self.root/OUTPUT).exists())
        self.untouched()

    def test_wrong_isin_or_pdf_receipt_blocks(self):
        self.prior["ISIN"]="INE000000000"
        self.prepare()
        with self.assertRaisesRegex(PDFComparisonBlocked,"PRIOR_COMPARATIVE_SOURCE"):
            run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.untouched()

    def test_wrong_fiscal_year_or_version_cannot_be_joined(self):
        self.prior["Comparative_Financial_Year"]="2021-22"
        self.prepare()
        with self.assertRaisesRegex(PDFComparisonBlocked,"PRIOR_COMPARATIVE_SOURCE"):
            run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.prior["Comparative_Financial_Year"]="2022-23"
        self.prior["Comparative_Review_Version"]="prior-year-comparative-v0"
        self.prepare()
        result=run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.assertEqual(result["new_two_year_comparisons"],0)
        self.assertFalse((self.root/OUTPUT).exists())
        self.untouched()

    def test_stale_v3_and_current_v4_numeric_same_pdf_not_ambiguous(self):
        # Actual Termux PDF review upgraded physical statement-page rules,
        # retaining the v3 numeric evidence alongside newly re-verified v4.
        stale={**self.current,
               "Section_Review_Version":"section-review-3-statement-table-cues"}
        write_rows(self.root/CURRENT_OUTPUT,CURRENT_FIELDS,
                   [stale,self.current])
        status=run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.assertEqual(status["new_two_year_comparisons"],1)
        self.assertEqual(status["current_version_comparison_records"],1)
        row=self.read_result()[0]
        self.assertEqual(row["Current_PDF_Section_Review_Version"],
                         SECTION_VERSION)
        self.assertEqual(row["Prior_Revenue_Operations_Cr"],"701.6872")
        self.assertEqual(row["Current_Revenue_Operations_Cr"],"777.4929")
        self.assertEqual(row["Source_Audited_Annual_XBRL_Passes_Added"],"0")
        self.assertEqual(row["Orders"],"DISABLED")
        self.untouched()

    def test_obsolete_comparison_version_preserved_but_current_revalidated(self):
        initial=run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.assertEqual(initial["new_two_year_comparisons"],1)
        archived=dict(self.read_result()[0],
                      Two_Year_Research_Version="source-locked-pdf-two-year-yoy-research-v1")
        write_rows(self.root/OUTPUT,FIELDS,[archived])
        latest=run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.assertEqual(latest["new_two_year_comparisons"],1)
        self.assertEqual(latest["total_two_year_comparisons"],2)
        self.assertEqual(latest["current_version_comparison_records"],1)
        self.assertEqual(latest["prior_version_comparison_records_preserved"],1)
        self.assertEqual({r["Two_Year_Research_Version"]
                          for r in self.read_result()},
                         {VERSION,"source-locked-pdf-two-year-yoy-research-v1"})
        again=run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.assertEqual(again["new_two_year_comparisons"],0)
        self.untouched()

    def test_duplicate_current_section_version_still_fails_closed(self):
        write_rows(self.root/CURRENT_OUTPUT,CURRENT_FIELDS,
                   [self.current,self.current])
        with self.assertRaisesRegex(PDFComparisonBlocked,
                                    "AMBIGUOUS_DUPLICATE_CURRENT_VERSION_SOURCE_ROW"):
            run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.assertFalse((self.root/OUTPUT).exists())
        self.untouched()

    def test_duplicate_source_versions_fail_not_random_choose(self):
        write_rows(self.root/PRIOR_OUTPUT,PRIOR_FIELDS,[self.prior,self.prior])
        with self.assertRaisesRegex(PDFComparisonBlocked,"AMBIGUOUS_DUPLICATE"):
            run(self.root,now=NOW,max_files=1,reader_cls=Reader)
        self.untouched()

    def test_negative_zero_base_never_reports_misleading_percent(self):
        for prior,now in [
            (Decimal("-12"),Decimal("6")),
            (Decimal("0"),Decimal("6")),
            (Decimal("-20"),Decimal("-10")),
        ]:
            pct,state=comparable_percent(now,prior)
            self.assertEqual(pct,"")
            self.assertEqual(state,"PERCENT_NOT_MEANINGFUL_PRIOR_ZERO_OR_NEGATIVE")
        self.untouched()

    def test_original_balance_conflict_never_becomes_trend(self):
        with self.assertRaisesRegex(
                PDFComparisonBlocked,"ORIGINAL_CURRENT_PDF_EVIDENCE_INCOMPLETE"):
            run(self.root,now=NOW,max_files=1,reader_cls=BadBalance)
        self.assertFalse((self.root/OUTPUT).exists())
        self.untouched()

    def test_bounds_and_no_network(self):
        with self.assertRaisesRegex(ValueError,"MAX_FILES"):
            run(self.root,max_files=3,reader_cls=Reader)
        self.untouched()


if __name__=="__main__":
    unittest.main()
