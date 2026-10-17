"""Synthetic, source-bound NSE annual report prior-FY column validations."""
import csv
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nse_annual_pdf_numeric_review import (
    financial_page_evidence,OUTPUT as CURRENT_OUTPUT,
    ROW_FIELDS as CURRENT_FIELDS,VERSION as CURRENT_VERSION,
)
from nse_annual_pdf_prior_year_comparatives import (
    run, comparative_evidence,prior_column,OUTPUT,VERSION,STATUS,CORE,
)
from nse_annual_pdf_section_review import (
    SectionReviewBlocked,LATEST_OUTPUT,FIELDS as SECTION_FIELDS,
    VERSION as SECTION_VERSION,
)
from nse_official_annual_pdf_auto_index import PDF_DIR
from test_nse_annual_pdf_numeric_review import (
    PDF,BS,PNL,Reader,WrappedPDFReader,LayoutModePDFReader,
)
NOW=datetime(2026,10,17,16,tzinfo=timezone.utc)
URL="https://nsearchives.nseindia.com/annual_reports/AR_24150_20MICRONS_2023_2024_20062024104857.pdf"
SHA=hashlib.sha256(PDF).hexdigest()


def save_csv(path,fields,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class WrongComparativeAssets(Reader):
    # FIRST/current-year figures are unchanged, so present-year v2
    # numeric verification stays complete. Previous total assets wrong.
    balance=BS.replace("TOTAL ASSETS 58,979.79 52,391.98",
                       "TOTAL ASSETS 58,979.79 51,391.98")


class WrongComparativeTax(Reader):
    pnl=PNL.replace("Total Tax Expenses 2,082.45 1,523.07",
                    "Total Tax Expenses 2,082.45 1,000.07")


class AnnualPDFPriorComparativeTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.path=self.root/PDF_DIR/"source_original.pdf"
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.path.write_bytes(PDF)
        self.receipt={
            "Symbol":"20MICRONS","Financial_Year":"2023-24",
            "Official_Report_URL":URL,"PDF_SHA256":SHA,
        }
        self.entry={"ISIN":"INE144J01027"}
        self.section={
            **self.receipt,
            "Review_Version":SECTION_VERSION,
            "Section_Review_State":"CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED",
            "Auditor_Page_1based":"201",
            "Consolidated_Balance_Sheet_Page_1based":"209",
            "Consolidated_Profit_Loss_Page_1based":"210",
            "Consolidated_Cash_Flow_Page_1based":"211",
        }
        found=financial_page_evidence(PDF,self.section,reader_cls=Reader)
        self.assertEqual(found["state"],
                         "COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED")
        self.current={
            **self.receipt,"ISIN":"INE144J01027",
            "Numeric_Review_Version":CURRENT_VERSION,
            "Section_Review_Version":SECTION_VERSION,
            "Review_State":found["state"],
            "Candidate_Core_Fields":"6",
            "Audited_Consolidated_Facts_Verified":"false",
            "Strict_Fundamental_Pass":"false","Orders":"DISABLED",
            **found["metrics"],
        }
        self.write_sources()
        self.identity=patch(
            "nse_annual_pdf_prior_year_comparatives.checked_sources",
            return_value=[(self.receipt,self.entry,self.path)])
        self.identity.start()
        self.addCleanup(self.identity.stop)
        self.protected={
            "annual_historical_results.csv":"SOURCE_VERIFIED_XBRL_HISTORY_UNTOUCHED",
            "final_fundamental_shortlist.csv":"STRICT_PASS_UNTOUCHED",
            "swing_watchlist.json":"ORDERS_DISABLED",
        }
        for path,text in self.protected.items():
            (self.root/path).write_text(text)

    def write_sources(self):
        save_csv(self.root/LATEST_OUTPUT,SECTION_FIELDS,[self.section])
        save_csv(self.root/CURRENT_OUTPUT,CURRENT_FIELDS,[self.current])

    def assert_untouched(self):
        for path,text in self.protected.items():
            self.assertEqual((self.root/path).read_text(),text)

    def read_candidates(self):
        with (self.root/OUTPUT).open(newline="") as file:
            return list(csv.DictReader(file))

    def test_real_source_priors_not_mistaken_for_independent_fy22_audit(self):
        result=run(self.root,max_files=1,now=NOW,reader_cls=Reader)
        self.assertEqual(result["reports_reviewed_this_run"],1)
        self.assertEqual(result["new_six_field_comparative_candidates"],1)
        self.assertEqual(result["separate_earlier_year_audited_xbrl_filings_verified"],0)
        self.assertEqual(result["strict_fundamental_passes_added"],0)
        self.assertEqual(result["source_index_api_calls"],0)
        self.assertEqual(result["orders"],"DISABLED")
        row=self.read_candidates()[0]
        self.assertEqual(row["Source_Report_Financial_Year"],"2023-24")
        self.assertEqual(row["Comparative_Financial_Year"],"2022-23")
        self.assertEqual(row["Comparative_Fiscal_Year_End"],"2023-03-31")
        self.assertEqual(row["Comparative_Evidence_Type"],
                         "PRIOR_YEAR_COLUMN_OF_LATER_ANNUAL_REPORT_ONLY")
        self.assertEqual(row["Candidate_Core_Fields"],"6")
        expected={
            "Revenue_Operations_Cr":"701.6872",
            "Net_Profit_Cr":"41.9617",
            "Basic_EPS_Rs":"11.85",
            "Total_Assets_Cr":"523.9198",
            "Total_Equity_Cr":"293.5786",
            "Total_Borrowings_Cr":"101.9190",
            "Noncurrent_Borrowings_Cr":"18.5693",
            "Current_Borrowings_Cr":"83.3497",
            "Noncontrolling_Interest_Cr":"1.0525",
        }
        self.assertEqual({key:row[key] for key in expected},expected)
        self.assertEqual(row["Comparative_Balance_Sheet_Check"],
                         "SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY")
        self.assertEqual(row["Comparative_Profit_Tax_Check"],
                         "SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY")
        self.assertEqual(row["Separately_Audited_Prior_Year_XBRL_Verified"],"false")
        self.assertEqual(row["Strict_Fundamental_Pass"],"false")
        self.assertEqual(row["Orders"],"DISABLED")
        source=json.loads(row["Evidence_Rows_JSON"])
        self.assertEqual(source["Revenue_Operations_Cr"]["source_page_1based"],210)
        self.assertEqual(source["Total_Assets_Cr"]["source_page_1based"],209)
        self.assertEqual(source["Borrowings_Current"]["source_page_1based"],209)
        self.assertEqual(source["Borrowings_Noncurrent"]["prior_year_lakhs_or_eps"],
                         "1856.93")
        repeat=run(self.root,max_files=1,now=NOW,reader_cls=Reader)
        self.assertEqual(repeat["reports_reviewed_this_run"],0)
        self.assertEqual(len(self.read_candidates()),1)
        self.assert_untouched()

    def test_wrapped_source_and_independent_layout_reconstruct_prior(self):
        for source in (WrappedPDFReader,LayoutModePDFReader):
            with self.subTest(reader=source.__name__):
                prior=comparative_evidence(
                    PDF,self.section,self.current,reader_cls=source)
                self.assertEqual(prior["candidate"]["Revenue_Operations_Cr"],
                                 "701.6872")
                self.assertEqual(prior["candidate"]["Total_Borrowings_Cr"],
                                 "101.9190")
                self.assertEqual(prior["state"],
                    "ALL_SIX_PRIOR_YEAR_COMPARATIVE_SOURCE_CANDIDATES_NOT_AUDIT_VERIFIED")
        self.assert_untouched()

    def test_prior_balance_conflict_quarantines_all_six_fields(self):
        found=comparative_evidence(PDF,self.section,self.current,
                                   reader_cls=WrongComparativeAssets)
        self.assertEqual(found["candidate"],{})
        self.assertEqual(found["balance_check"],"SOURCE_ARITHMETIC_CONFLICT")
        self.assertEqual(found["state"],
                         "PRIOR_YEAR_COMPARATIVES_ARITHMETIC_REVIEW_REQUIRED")
        self.assertIn("PRIOR_YEAR_BALANCE_ARITHMETIC_CONFLICT",found["issues"])
        self.assert_untouched()

    def test_prior_tax_conflict_quarantines_all_six_fields(self):
        found=comparative_evidence(PDF,self.section,self.current,
                                   reader_cls=WrongComparativeTax)
        self.assertEqual(found["candidate"],{})
        self.assertEqual(found["profit_check"],"SOURCE_ARITHMETIC_CONFLICT")
        self.assertIn("PRIOR_YEAR_PROFIT_TAX_ARITHMETIC_CONFLICT",found["issues"])
        self.assert_untouched()

    def test_three_or_one_numeric_year_cells_not_accepted(self):
        row={
            "page_1based":209,
            "raw_source_row":"Total equity 35,295.37 29,357.86 99.55",
            "current_lakhs_or_eps":"35295.37",
        }
        with self.assertRaisesRegex(SectionReviewBlocked,"EXACTLY_TWO"):
            prior_column({"Total_Equity_Cr":row},"Total_Equity_Cr",
                         Decimal("35295.37"),209)
        self.assert_untouched()

    def test_conflicting_cached_current_fy_fails_closed(self):
        self.current["Revenue_Operations_Cr"]="999.9999"
        self.write_sources()
        with self.assertRaisesRegex(SectionReviewBlocked,
                                     "CACHED_CURRENT_FINANCIAL"):
            run(self.root,max_files=1,now=NOW,reader_cls=Reader)
        self.assertFalse((self.root/OUTPUT).exists())
        self.assert_untouched()

    def test_tampered_original_pdf_sha_blocks_comparative_extraction(self):
        self.path.write_bytes(PDF+b"changed")
        with self.assertRaisesRegex(SectionReviewBlocked,
                                     "SOURCE_PDF_HASH_CHANGED"):
            run(self.root,max_files=1,now=NOW,reader_cls=Reader)
        self.assertFalse((self.root/OUTPUT).exists())
        self.assert_untouched()

    def test_wrong_isin_or_old_numeric_version_blocks_source(self):
        self.current["ISIN"]="INE999999999"
        self.write_sources()
        with self.assertRaisesRegex(SectionReviewBlocked,"ISIN_MISMATCH"):
            run(self.root,max_files=1,now=NOW,reader_cls=Reader)
        self.current["ISIN"]=self.entry["ISIN"]
        self.current["Numeric_Review_Version"]="numeric-evidence-candidate-v1"
        self.write_sources()
        result=run(self.root,max_files=1,now=NOW,reader_cls=Reader)
        self.assertEqual(result["reports_reviewed_this_run"],0)
        self.assertFalse((self.root/OUTPUT).exists())
        self.assert_untouched()

    def test_no_fy23_24_source_no_prior_fy22_23_candidate(self):
        self.receipt["Financial_Year"]="2022-23"
        result=run(self.root,max_files=1,now=NOW,reader_cls=Reader)
        self.assertEqual(result["reports_reviewed_this_run"],0)
        self.assertFalse((self.root/OUTPUT).exists())
        self.assert_untouched()

    def test_incomplete_current_numeric_evidence_is_deferred_not_blocked(self):
        # Matches a genuine section-reviewed report whose six source
        # amounts cannot yet pass arithmetic/date/unit verification.
        self.current.update({
            "Review_State":"INCOMPLETE_OR_AMBIGUOUS_SOURCE_NUMBERS_REVIEW_REQUIRED",
            "Candidate_Core_Fields":"0",
        })
        for key in CORE:
            self.current.pop(key,None)
        self.write_sources()
        result=run(self.root,max_files=1,now=NOW,reader_cls=Reader)
        self.assertEqual(result["state"],
                         "ORIGINAL_NSE_PDF_PRIOR_YEAR_COMPARATIVE_REVIEW_UPDATED")
        self.assertEqual(result["reports_reviewed_this_run"],0)
        self.assertEqual(result["reports_deferred_incomplete_current_numeric_evidence"],1)
        self.assertEqual(result["new_six_field_comparative_candidates"],0)
        self.assertEqual(result["deferred_incomplete_source_preview"][0]["symbol"],
                         "20MICRONS")
        self.assertFalse((self.root/OUTPUT).exists())
        self.assert_untouched()

    def test_incomplete_new_pdf_does_not_block_complete_other_pdf(self):
        incomplete_receipt={
            **self.receipt,
            "Symbol":"SYNTHETIC2",
            "Official_Report_URL":"https://nsearchives.nseindia.com/annual_reports/AR_999_SYNTHETIC2_2023_2024_A_1.pdf",
        }
        incomplete_entry={"ISIN":"INE900A01019"}
        incomplete_section={**self.section,**incomplete_receipt}
        incomplete_current={
            **self.current,**incomplete_receipt,
            "ISIN":incomplete_entry["ISIN"],
            "Review_State":"INCOMPLETE_OR_AMBIGUOUS_SOURCE_NUMBERS_REVIEW_REQUIRED",
            "Candidate_Core_Fields":"0",
        }
        for key in CORE:
            incomplete_current.pop(key,None)
        save_csv(self.root/LATEST_OUTPUT,SECTION_FIELDS,
                 [self.section,incomplete_section])
        save_csv(self.root/CURRENT_OUTPUT,CURRENT_FIELDS,
                 [self.current,incomplete_current])
        with patch("nse_annual_pdf_prior_year_comparatives.checked_sources",
                   return_value=[
                       (incomplete_receipt,incomplete_entry,self.path),
                       (self.receipt,self.entry,self.path),
                   ]):
            result=run(self.root,max_files=2,now=NOW,reader_cls=Reader)
        self.assertEqual(result["reports_reviewed_this_run"],1)
        self.assertEqual(result["new_six_field_comparative_candidates"],1)
        self.assertEqual(result["reports_deferred_incomplete_current_numeric_evidence"],1)
        self.assertEqual(len(self.read_candidates()),1)
        self.assertEqual(self.read_candidates()[0]["Symbol"],"20MICRONS")
        self.assertEqual(result["strict_fundamental_passes_added"],0)
        self.assert_untouched()

    def test_incomplete_label_cannot_hide_cached_numeric_source_values(self):
        self.current.update({
            "Review_State":"INCOMPLETE_OR_AMBIGUOUS_SOURCE_NUMBERS_REVIEW_REQUIRED",
            "Candidate_Core_Fields":"0",
        })
        self.write_sources()  # Still contains six present-year values.
        with self.assertRaisesRegex(
                SectionReviewBlocked,"INCOMPLETE_CURRENT_PDF_SOURCE_STATE_CONFLICT"):
            run(self.root,max_files=1,now=NOW,reader_cls=Reader)
        self.assertFalse((self.root/OUTPUT).exists())
        self.assert_untouched()

    def test_bounded_batch_never_triggers_network_or_trade(self):
        with self.assertRaisesRegex(ValueError,"MAX_FILES"):
            run(self.root,max_files=3,reader_cls=Reader)
        self.assert_untouched()


if __name__=="__main__":
    unittest.main()
