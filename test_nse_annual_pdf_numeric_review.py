"""NSE archived audited annual PDF numeric-row candidate review regressions."""
from datetime import datetime,timezone
import csv
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nse_annual_pdf_numeric_review import (
    run, financial_page_evidence, OUTPUT, VERSION, MAX_FILES,
)
from nse_annual_pdf_section_review import (
    SectionReviewBlocked,LATEST_OUTPUT,VERSION as SECTION_VERSION,FIELDS,
)
from nse_official_annual_pdf_auto_index import PDF_DIR

NOW=datetime(2026,10,17,11,tzinfo=timezone.utc)
PDF=b"%PDF-1.6\n"+b"T"*5100+b"\n%%EOF"
URL="https://nsearchives.nseindia.com/annual_reports/AR_24150_20MICRONS_2023_2024_20062024104857.pdf"
SHA=hashlib.sha256(PDF).hexdigest()
BS="""Consolidated Balance Sheet
as at 31st March, 2024
(In Lakhs)
Particulars 31st March, 2024 31st March, 2023
Total Non-current Assets 25,780.17 24,954.37
Total Current Assets 33,199.62 27,437.61
TOTAL ASSETS 58,979.79 52,391.98
II. EQUITY AND LIABILITIES
Total equity 35,295.37 29,357.86
2 Non Controlling Interest 114.40 105.25
3 Liabilities
Non-current Liabilities
(i) Borrowings 19 1,241.25 1,856.93
Total Non Current Liabilities 4,616.07 4,952.34
4 Current Liabilities
(i) Borrowings 23 10,320.78 8,334.97
Total Current Liabilities 18,953.96 17,976.54
Total Liabilities 23,570.02 22,928.88
TOTAL EQUITY AND LIABILITIES 58,979.79 52,391.98
"""
PNL="""Consolidated Statement of Profit and Loss
for the year ended 31st March, 2024
(In Lakhs)
Particulars For the year ended 31st March, 2024 31st March, 2023
Revenue from Operations 29 77,749.29 70,168.72
Profit Before Tax (VII - VIII) 7,698.49 5,719.24
Total Tax Expenses 2,082.45 1,523.07
XI. Profit for the period (IX-X) 5,616.03 4,196.17
Earnings per equity share of Face Value of Rs 5 each
Basic 15.89 11.85
Diluted 15.89 11.85
"""
CF="""Consolidated Statement of Cash Flow
31st March, 2024
(In Lakhs)
Cash Flow from Operating Activities 5,444.34 6,319.67
Cash Flow from Investing Activities (2,311.49) (1,767.42)
"""


class Page:
    def __init__(self,text):
        self.content=text
    def extract_text(self):
        return self.content


class Reader:
    balance=BS
    pnl=PNL
    def __init__(self,stream,strict=True):
        assert stream.read(5)==b"%PDF-"
        self.is_encrypted=False
        self.pages=[Page("statutory report") for _ in range(281)]
        self.pages[200]=Page(
            "Independent Auditor's Report on Consolidated Statements. "
            "Opinion: We have audited the consolidated financial statements.")
        self.pages[208]=Page(self.balance)
        self.pages[209]=Page(self.pnl)
        self.pages[210]=Page(CF)


class SplitCellPage(Page):
    def extract_text(self,extraction_mode=None):
        return self.content


class WrappedPDFReader(Reader):
    """Simulate actual multi-line pypdf extraction from a 2-year table."""
    balance=(BS.replace("Total equity 35,295.37 29,357.86",
                        "Total equity\n35,295.37\n29,357.86")
               .replace("(i) Borrowings 19 1,241.25 1,856.93",
                        "(i) Borrowings 19\n1,241.25 1,856.93")
               .replace("(i) Borrowings 23 10,320.78 8,334.97",
                        "(i) Borrowings 23\n10,320.78\n8,334.97"))
    pnl=PNL.replace("Revenue from Operations 29 77,749.29 70,168.72",
                    "Revenue from Operations 29\n77,749.29\n70,168.72")


class LayoutOnlyPage:
    def __init__(self,primary,layout):
        self.primary=primary
        self.layout=layout

    def extract_text(self,extraction_mode=None):
        if extraction_mode=="layout":
            return self.layout
        return self.primary


class LayoutModePDFReader(Reader):
    """Simulates pypdf reading numbers in a separate column by default."""
    def __init__(self,stream,strict=True):
        super().__init__(stream,strict=strict)
        primary_bs=(BS.replace("Total equity 35,295.37 29,357.86",
                               "Total equity")
                      .replace("(i) Borrowings 19 1,241.25 1,856.93",
                               "(i) Borrowings 19")
                      .replace("(i) Borrowings 23 10,320.78 8,334.97",
                               "(i) Borrowings 23"))
        primary_pnl=PNL.replace(
            "Revenue from Operations 29 77,749.29 70,168.72",
            "Revenue from Operations 29")
        self.pages[208]=LayoutOnlyPage(primary_bs,BS)
        self.pages[209]=LayoutOnlyPage(primary_pnl,PNL)


class ConflictingTextModesPDFReader(Reader):
    def __init__(self,stream,strict=True):
        super().__init__(stream,strict=strict)
        self.pages[209]=LayoutOnlyPage(
            PNL,PNL.replace("Revenue from Operations 29 77,749.29 70,168.72",
                            "Revenue from Operations 29 77,749.30 70,168.72"))


class BorrowingsWrongSectionsReader(Reader):
    balance=BS.replace(
        "Non-current Liabilities\n(i) Borrowings 19 1,241.25 1,856.93",
        "Current Liabilities\n(i) Borrowings 19 1,241.25 1,856.93")


class MissingRowNeighborReader(Reader):
    pnl=PNL.replace("Revenue from Operations 29 77,749.29 70,168.72",
                    "Revenue from Operations 29\n"
                    "II. Other income 30 406.51 293.38")


class NoUnits(Reader):
    balance=BS.replace("(In Lakhs)","(INR)")
    pnl=PNL.replace("(In Lakhs)","(INR)")


class WrongYearOrder(Reader):
    pnl=PNL.replace("31st March, 2024 31st March, 2023",
                    "31st March, 2023 31st March, 2024").replace(
                        "for the year ended 31st March, 2024",
                        "for the year ended in financial statements")


class BadBalance(Reader):
    balance=BS.replace("Total Liabilities 23,570.02", "Total Liabilities 22,570.02")


class MissingBorrowings(Reader):
    balance=BS.replace("(i) Borrowings 23 10,320.78 8,334.97",
                       "(i) Borrowings 23 Not reported")


class AnnualPDFNumericEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.private=self.root/PDF_DIR
        self.private.mkdir(parents=True,exist_ok=True)
        self.pdf=self.private/"original.pdf"
        self.pdf.write_bytes(PDF)
        self.receipt={
            "Symbol":"20MICRONS","Financial_Year":"2023-24",
            "Official_Report_URL":URL,"PDF_SHA256":SHA,
        }
        self.entry={"ISIN":"INE144J01027"}
        self.pages={
            "Symbol":"20MICRONS","Financial_Year":"2023-24",
            "Official_Report_URL":URL,"PDF_SHA256":SHA,
            "Review_Version":SECTION_VERSION,
            "Section_Review_State":"CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED",
            "Auditor_Page_1based":"201",
            "Consolidated_Balance_Sheet_Page_1based":"209",
            "Consolidated_Profit_Loss_Page_1based":"210",
            "Consolidated_Cash_Flow_Page_1based":"211",
        }
        self.write_latest([self.pages])
        self.catalog=patch("nse_annual_pdf_numeric_review.checked_sources",
                           return_value=[(self.receipt,self.entry,self.pdf)])
        self.catalog.start()
        self.addCleanup(self.catalog.stop)
        self.protected={
            "annual_historical_results.csv":"VERIFIED_HISTORY_MUST_NOT_CHANGE",
            "final_fundamental_shortlist.csv":"STRICT_SCREEN_NOT_PROMOTED",
            "swing_watchlist.json":"NO_TRADES_OR_ALERTS",
        }
        for name,data in self.protected.items():
            (self.root/name).write_text(data)

    def write_latest(self,rows):
        with (self.root/LATEST_OUTPUT).open("w",newline="") as f:
            out=csv.DictWriter(f,fieldnames=FIELDS)
            out.writeheader()
            out.writerows(rows)

    def assert_protected(self):
        for name,data in self.protected.items():
            self.assertEqual((self.root/name).read_text(),data)

    def read_output(self):
        with (self.root/OUTPUT).open(newline="") as f:
            return list(csv.DictReader(f))

    def test_actual_source_figures_candidate_crore_units_not_xbrl_verified(self):
        result=run(self.root,max_files=1,now=NOW,reader_cls=Reader)
        self.assertEqual(result["new_reports_read"],1)
        self.assertEqual(result["new_complete_six_field_candidate_reports"],1)
        self.assertEqual(result["strict_fundamental_passes_added"],0)
        row=self.read_output()[0]
        self.assertEqual(row["Statement_Units"],"INR_LAKHS")
        self.assertEqual(row["Review_State"],
                         "COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED")
        self.assertEqual(row["Revenue_Operations_Cr"],"777.4929")
        self.assertEqual(row["Net_Profit_Cr"],"56.1603")
        self.assertEqual(row["Basic_EPS_Rs"],"15.89")
        self.assertEqual(row["Total_Assets_Cr"],"589.7979")
        self.assertEqual(row["Total_Equity_Cr"],"352.9537")
        self.assertEqual(row["Noncontrolling_Interest_Cr"],"1.1440")
        self.assertEqual(row["Noncurrent_Borrowings_Cr"],"12.4125")
        self.assertEqual(row["Current_Borrowings_Cr"],"103.2078")
        self.assertEqual(row["Total_Borrowings_Cr"],"115.6203")
        self.assertEqual(row["Balance_Sheet_Check"],
                         "SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY")
        self.assertEqual(row["Profit_Tax_Check"],
                         "SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY")
        self.assertEqual(row["Audited_Consolidated_Facts_Verified"],"false")
        self.assertEqual(row["Orders"],"DISABLED")
        evidence=json.loads(row["Evidence_Rows_JSON"])
        self.assertEqual(evidence["Revenue_Operations_Cr"]["page_1based"],210)
        self.assertEqual(evidence["Total_Assets_Cr"]["page_1based"],209)
        self.assertEqual(evidence["Borrowings_Current"]["page_1based"],209)
        # Review is idempotent under the same immutable PDF SHA/version.
        self.assertEqual(run(self.root,max_files=1,now=NOW,reader_cls=Reader)[
            "new_reports_read"],0)
        self.assert_protected()

    def test_wrapped_two_year_cells_from_original_pdf_safe_reconstruction(self):
        # Real PDF text sometimes puts the printed annual cells on
        # separate lines even though the visible table row is one row.
        details=financial_page_evidence(PDF,self.pages,reader_cls=WrappedPDFReader)
        self.assertEqual(details["state"],
                         "COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED")
        self.assertEqual(details["metrics"]["Revenue_Operations_Cr"],"777.4929")
        self.assertEqual(details["metrics"]["Total_Equity_Cr"],"352.9537")
        self.assertEqual(details["metrics"]["Total_Borrowings_Cr"],"115.6203")
        self.assertEqual(details["borrowings_check"],
                         "BOTH_ROW_CANDIDATES_FOUND_UNVERIFIED")
        self.assert_protected()

    def test_independent_pdf_layout_can_rescue_split_table_columns(self):
        details=financial_page_evidence(PDF,self.pages,reader_cls=LayoutModePDFReader)
        self.assertEqual(details["state"],
                         "COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED")
        self.assertEqual(details["metrics"]["Revenue_Operations_Cr"],"777.4929")
        self.assertEqual(details["metrics"]["Total_Equity_Cr"],"352.9537")
        self.assertEqual(details["metrics"]["Total_Borrowings_Cr"],"115.6203")
        self.assertEqual(details["raw_evidence"]["Total_Equity_Cr"][
            "pdf_extraction_mode"],"LAYOUT_TEXT")
        self.assert_protected()

    def test_if_both_pdf_text_modes_disagree_do_not_guess_source_number(self):
        details=financial_page_evidence(PDF,self.pages,
                                        reader_cls=ConflictingTextModesPDFReader)
        self.assertEqual(details["metrics"],{})
        self.assertIn(
            "Revenue_Operations_Cr:CONFLICTING_PDF_TEXT_EXTRACTION_MODES",
            details["issues"])
        self.assert_protected()

    def test_never_consume_neighbor_named_financial_row_values(self):
        details=financial_page_evidence(PDF,self.pages,
                                        reader_cls=MissingRowNeighborReader)
        self.assertEqual(details["metrics"],{})
        self.assertIn("Revenue_Operations_Cr:MISSING_OR_AMBIGUOUS_TWO_COLUMN_ROW",
                      details["issues"])
        self.assert_protected()

    def test_borrowings_must_be_in_noncurrent_then_current_sections(self):
        details=financial_page_evidence(PDF,self.pages,
                                        reader_cls=BorrowingsWrongSectionsReader)
        self.assertEqual(details["metrics"],{})
        self.assertIn("BORROWINGS_REQUIRES_TWO_INDEPENDENT_SOURCE_ROWS",
                      details["issues"])
        self.assert_protected()

    def test_existing_v1_failure_automatically_rechecked_with_v2(self):
        self.root.joinpath(OUTPUT).write_text(
            "Symbol,Financial_Year,PDF_SHA256,Numeric_Review_Version,Review_State\n"
            + f"20MICRONS,2023-24,{SHA},numeric-evidence-candidate-v1,"
              "INCOMPLETE_OR_AMBIGUOUS_SOURCE_NUMBERS_REVIEW_REQUIRED\n"
        )
        result=run(self.root,max_files=1,now=NOW,reader_cls=WrappedPDFReader)
        self.assertEqual(result["new_reports_read"],1)
        with (self.root/OUTPUT).open(newline="") as f:
            data=list(csv.DictReader(f))
        self.assertEqual(len(data),2)
        self.assertEqual({r["Numeric_Review_Version"] for r in data},
                         {"numeric-evidence-candidate-v1",VERSION})
        self.assertEqual([r for r in data if r["Numeric_Review_Version"]==VERSION][0][
            "Candidate_Core_Fields"],"6")
        self.assert_protected()

    def test_unit_or_date_ambiguity_quarantines_all_numeric_candidates(self):
        for cls in (NoUnits,WrongYearOrder):
            with self.subTest(cls=cls.__name__):
                data=financial_page_evidence(PDF,self.pages,reader_cls=cls)
                self.assertEqual(data["metrics"],{})
                self.assertEqual(data["state"],
                                 "INCOMPLETE_OR_AMBIGUOUS_SOURCE_NUMBERS_REVIEW_REQUIRED")
        self.assert_protected()

    def test_missing_borrowings_does_not_mark_six_fields_complete(self):
        d=financial_page_evidence(PDF,self.pages,reader_cls=MissingBorrowings)
        self.assertEqual(d["metrics"],{})
        self.assertIn("BORROWINGS_REQUIRES_TWO_INDEPENDENT_SOURCE_ROWS",d["issues"])

    def test_balance_sheet_arithmetic_conflict_stops_candidate_values(self):
        d=financial_page_evidence(PDF,self.pages,reader_cls=BadBalance)
        self.assertEqual(d["balance_check"],"ARITHMETIC_CONFLICT")
        self.assertEqual(d["metrics"],{})
        self.assert_protected()

    def test_obsolete_section_review_v2_is_never_trusted_for_numbers(self):
        older=dict(self.pages,Review_Version="section-review-2-adaptive")
        self.write_latest([older])
        result=run(self.root,max_files=1,now=NOW,reader_cls=Reader)
        self.assertEqual(result["new_reports_read"],0)
        self.assertFalse((self.root/OUTPUT).exists())
        self.assert_protected()

    def test_changed_cached_pdf_sha_rejected_without_writing_candidate(self):
        self.pdf.write_bytes(PDF+b"tampered")
        with self.assertRaisesRegex(SectionReviewBlocked,"BYTES_CHANGED"):
            run(self.root,max_files=1,now=NOW,reader_cls=Reader)
        self.assertFalse((self.root/OUTPUT).exists())
        self.assert_protected()

    def test_duplicate_auditor_and_balance_page_rejected(self):
        record=dict(self.pages,Auditor_Page_1based="209")
        with self.assertRaisesRegex(SectionReviewBlocked,"DUPLICATE_SECTION"):
            financial_page_evidence(PDF,record,reader_cls=Reader)

    def test_max_batch_size_and_no_external_fetch(self):
        with self.assertRaisesRegex(ValueError,"BOUNDED_MAX_FILES"):
            run(self.root,max_files=3,reader_cls=Reader)
        self.assert_protected()


if __name__=="__main__":
    unittest.main()
