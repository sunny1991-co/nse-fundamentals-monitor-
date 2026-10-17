"""Offline regressions for source-bound, bounded automatic NSE annual PDF review."""
import csv
from datetime import datetime,timezone
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nse_mainboard_discovery import CATALOG
from nse_official_annual_pdf_auto_index import (
    PDF_DIR, PDF_LOG, OUTPUT as PDF_INDEX, PDF_FIELDS, OUTPUT_FIELDS,
)
from nse_annual_pdf_section_review import (
    SectionReviewBlocked,run,inspect_pdf,STATUS,OUTPUT,LATEST_OUTPUT,
    MAX_SCAN_PAGES,VERSION,page_indices,dense_financial_band,
)


URL="https://nsearchives.nseindia.com/annual_reports/AR_222_TESTEQ_2022_2023_A_14.pdf"
SYMBOL="TESTEQ"
ISIN="INE001A01011"
BODY=b"%PDF-1.6\n"+b"x"*4100+b"\n%%EOF"
NOW=datetime(2026,10,17,10,tzinfo=timezone.utc)


class FakePage:
    def __init__(self,i,year="2022-23"):
        self.i=i
        self.year=year

    def extract_text(self):
        if self.i==0:
            return "Annual Report "+self.year+" of TESTEQ Limited"
        if self.i==123:
            return ("Independent Auditor’s Report Opinion We have audited "
                    "Consolidated Financial Statements. Basis for Opinion")
        if self.i==124:
            return ("Consolidated Balance Sheet as at 31 March 2023 (in lakhs) "
                    "Total Assets 110.00 Total Equity and Liabilities 110.00")
        if self.i==125:
            return ("Consolidated Statement of Profit and Loss for year ended "
                    "31 March 2023 Revenue from Operations 110.00 "
                    "Profit for the period 10.00")
        if self.i==126:
            return ("Consolidated Cash Flow Statement for year ended 31 March 2023 "
                    "Cash Flow from Operating Activities 8.00 "
                    "Cash Flow from Investing Activities -2.00")
        return "Directors and statutory disclosures"


class FakeReader:
    pages=[]
    def __init__(self,stream,strict=True):
        assert stream.read(5)==b"%PDF-"
        self.is_encrypted=False
        self.pages=[FakePage(i) for i in range(180)]


class FakeSparseGapPage:
    def __init__(self,index):
        self.index=index

    def extract_text(self):
        if self.index==0:
            return "Annual Report 2022-23 of TESTEQ Limited"
        labels={
            205:("Independent Auditor's Report on Consolidated Financial "
                 "Statements. Opinion We have audited the Group. Basis for Opinion"),
            206:("Consolidated Balance Sheet for 31 March 2023 "
                 "TOTAL ASSETS 1,000.00 TOTAL EQUITY AND LIABILITIES 1,000.00"),
            207:("Consolidated Statement of Profit and Loss "
                 "Revenue from Operations 500.00 Profit for the year 100.00"),
            208:("Consolidated Cash Flow Statement "
                 "Cash Flow from Operating Activities 100.00 "
                 "Cash Flow from Investing Activities -40.00"),
        }
        return labels.get(self.index,"Directors statutory disclosures")


class FakeSparseGapReader(FakeReader):
    def __init__(self,stream,strict=True):
        super().__init__(stream,strict=strict)
        self.pages=[FakeSparseGapPage(i) for i in range(280)]


class FakeTwentyMicronsFinancialSectionPage:
    """Synthetic PDF layout matching observed 20MICRONS page placement."""
    def __init__(self,i):
        self.i=i

    def extract_text(self):
        if self.i==0:
            return "20 MICRONS Annual Report 2023-24"
        if self.i==199:
            # Original scanner falsely reported all four headings at
            # physical page 200, which is ONLY a contents/section index.
            return ("178 Consolidated Independent Auditor's Report "
                    "186 Consolidated Balance Sheet "
                    "187 Consolidated Statement of Profit and Loss "
                    "188 Consolidated Cash Flow Statement")
        if self.i==200:
            # Physical PDF page 201: actual auditor's report.
            return ("Independent Auditor's Report On Consolidated Financial "
                    "Statements. Opinion We have audited the accompanying "
                    "Consolidated Financial Statements. Basis for Opinion.")
        if self.i==208:
            return ("Consolidated Balance Sheet as at 31 March 2024 "
                    "TOTAL ASSETS 58979.79 TOTAL EQUITY AND LIABILITIES 58979.79")
        if self.i==209:
            return ("Consolidated Statement of Profit and Loss for the year ended "
                    "31 March 2024 Revenue from Operations 77749.29 "
                    "Profit for the period 5616.03")
        if self.i==210:
            return ("Consolidated Statement of Cash Flow for 31 March 2024 "
                    "Cash Flow from Operating Activities 5444.34 "
                    "Cash Flow from Investing Activities -1988.19")
        return "Statutory reports and notes."


class FakeTwentyMicronsFinancialReader(FakeReader):
    def __init__(self,stream,strict=True):
        super().__init__(stream,strict=strict)
        self.pages=[FakeTwentyMicronsFinancialSectionPage(i) for i in range(281)]


class FakeLongConsolidatedAuditorPage:
    """Synthetic FY2023-24 layout matching published 21STCENMGM headings."""
    def __init__(self,i):
        self.i=i

    def extract_text(self):
        if self.i==0:
            return "Twentyfirst Century Management Services Annual Report 2023-24"
        if self.i==1:
            return ("CONTENTS: Consolidated Balance Sheet, Consolidated "
                    "Profit & Loss Statement, Consolidated Cash Flow Statement")
        if self.i==117:  # physical page 118
            return ("Independent Auditor's Report. We have audited the "
                    "Consolidated Financial Statements. Basis for Opinion.")
        if self.i==125:  # physical page 126; generic TOTAL, not TOTAL ASSETS
            return ("CONSOLIDATED BALANCE SHEET AS ON 31ST MARCH 2024. "
                    "ASSETS: Current Assets; TOTAL 5,556.51 3,776.48. "
                    "EQUITY AND LIABILITIES Equity Share capital "
                    "1,050.00 1,050.00. TOTAL 5,556.51 3,776.48.")
        if self.i==126:  # physical page 127; ampersand and Profit/(loss)
            return ("CONSOLIDATED PROFIT & LOSS STATEMENT FOR THE YEAR ENDED "
                    "31ST MARCH 2024. Revenue From Operations 3,855.42 (916.96). "
                    "Profit/(loss) before tax 3292.60 (1793.61).")
        if self.i==127:  # physical page 128
            return ("CONSOLIDATED CASH FLOW STATEMENT FOR THE YEAR ENDED "
                    "31ST MARCH 2024. CASH FLOW FROM OPERATING ACTIVITIES "
                    "and CASH FLOW FROM INVESTING ACTIVITIES.")
        return "Board report and notes to accounts"


class FakeLongConsolidatedAuditorReader(FakeReader):
    def __init__(self,stream,strict=True):
        super().__init__(stream,strict=strict)
        self.pages=[FakeLongConsolidatedAuditorPage(i) for i in range(152)]


class FakeSplitConsolidatedBalancePage:
    """63MOONS FY2023-24 source-like split BS; physical pages 179-180."""
    def __init__(self,index,closing_prior="3,46,585.27"):
        self.index=index
        self.closing_prior=closing_prior

    def extract_text(self):
        if self.index==0:
            return "63 moons technologies limited Annual Report 2023-24"
        if self.index==168:
            return ("INDEPENDENT AUDITOR'S REPORT. We have audited "
                    "consolidated financial statements. Basis for opinion.")
        if self.index==178:
            return ("FINANCIALS | CONSOLIDATED. CONSOLIDATED BALANCE SHEET "
                    "as at March 31, 2024 (IN LAKHS) ASSETS "
                    "TOTAL NON-CURRENT ASSETS 1,76,138.97 1,45,699.07 "
                    "TOTAL CURRENT ASSETS 1,88,391.42 2,00,886.20 "
                    "TOTAL ASSETS 3,64,530.39 3,46,585.27")
        if self.index==179:
            return ("CONSOLIDATED BALANCE SHEET (CONTD.) "
                    "as at March 31, 2024 (IN LAKHS) "
                    "EQUITY AND LIABILITIES EQUITY Equity share capital "
                    "921.57 921.57 TOTAL EQUITY 3,31,810.87 3,09,050.72 "
                    "TOTAL LIABILITIES 32,719.52 37,534.55 "
                    "TOTAL EQUITY AND LIABILITIES 3,64,530.39 "+
                    self.closing_prior)
        if self.index==180:
            return ("CONSOLIDATED STATEMENT OF PROFIT AND LOSS "
                    "Revenue from Operations 8,000.00 7,000.00 "
                    "Profit before tax 2,000.00 (1,000.00)")
        if self.index==184:
            return ("CONSOLIDATED STATEMENT OF CASH FLOW "
                    "CASH FLOW FROM OPERATING ACTIVITIES 4,000.00 "
                    "CASH FLOW FROM INVESTING ACTIVITIES (3,000.00)")
        return "Statutory disclosures and explanatory notes."


class FakeSplitConsolidatedBalanceReader(FakeReader):
    closing_prior="3,46,585.27"
    def __init__(self,stream,strict=True):
        super().__init__(stream,strict=strict)
        self.pages=[FakeSplitConsolidatedBalancePage(i,self.closing_prior)
                    for i in range(248)]


class FakeSplitBalanceMismatchReader(FakeSplitConsolidatedBalanceReader):
    closing_prior="3,46,585.28"


class FakeSectionIndexOnlyReader(FakeReader):
    def __init__(self,stream,strict=True):
        super().__init__(stream,strict=strict)
        self.pages=[FakeTwentyMicronsFinancialSectionPage(i) for i in range(281)]
        for i in (200,208,209,210):
            self.pages[i]=FakePage(-1)


class FakeWrongFY(FakeReader):
    def __init__(self,stream,strict=True):
        super().__init__(stream,strict=strict)
        self.pages=[FakePage(i,"2020-21") for i in range(180)]


def csv_write(path,fields,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class AnnualArchivedSectionReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.url=URL
        sha=hashlib.sha256(BODY).hexdigest()
        name=hashlib.sha256(self.url.encode()).hexdigest()[:28]+".pdf"
        self.pdf=self.root/PDF_DIR/name
        self.pdf.parent.mkdir(parents=True,exist_ok=True)
        self.pdf.write_bytes(BODY)
        csv_write(self.root/CATALOG,
                  ["Symbol","ISIN","Series","Company"],
                  [{"Symbol":SYMBOL,"ISIN":ISIN,"Series":"EQ","Company":"Test Limited"}])
        self.index=[{
            "Symbol":SYMBOL,"ISIN":ISIN,"Company":"Test",
            "Financial_Year":"2022-23","From_Year":"2022","To_Year":"2023",
            "Official_Report_URL":self.url,
            "NSE_Reported_Company":"Test Limited","Submission_Type":"Annual",
            "Broadcast_Date":"2023-08-01",
            "Original_Metadata_SHA256":"a"*64,
            "Indexed_At_UTC":NOW.isoformat(),
            "Verification_State":"OFFICIAL_PDF_CANDIDATE_UNAUDITED_NUMERIC_FACTS",
        }]
        csv_write(self.root/PDF_INDEX,OUTPUT_FIELDS,self.index)
        self.receipt=[{
            "Symbol":SYMBOL,"Financial_Year":"2022-23",
            "Official_Report_URL":self.url,
            "Source_Index_SHA256":"a"*64,
            "PDF_SHA256":sha,"PDF_Bytes":len(BODY),
            "Cached_Private_Path":(PDF_DIR/name).as_posix(),
            "Downloaded_At_UTC":NOW.isoformat(),
            "Review_Status":"REQUIRES_AUDITED_CONSOLIDATED_FULL_YEAR_NUMERIC_RECONCILIATION",
        }]
        csv_write(self.root/PDF_LOG,PDF_FIELDS,self.receipt)
        self.identity=patch("nse_annual_pdf_section_review.verified_discovered_symbols",
                            return_value={SYMBOL})
        self.identity.start()
        self.addCleanup(self.identity.stop)
        (self.root/"annual_historical_results.csv").write_text("DO_NOT_CHANGE")
        (self.root/"final_fundamental_shortlist.csv").write_text("DO_NOT_CHANGE")
        (self.root/"swing_watchlist.json").write_text("DO_NOT_CHANGE")

    def assert_protected(self):
        self.assertEqual((self.root/"annual_historical_results.csv").read_text(),"DO_NOT_CHANGE")
        self.assertEqual((self.root/"final_fundamental_shortlist.csv").read_text(),"DO_NOT_CHANGE")
        self.assertEqual((self.root/"swing_watchlist.json").read_text(),"DO_NOT_CHANGE")

    def test_source_bound_pdf_sections_located_without_financial_verified(self):
        r=run(self.root,max_files=1,now=NOW,reader_cls=FakeReader)
        self.assertEqual(r["reports_reviewed_this_run"],1)
        self.assertEqual(r["financial_facts_added"],0)
        self.assertFalse(r["trading_enabled"])
        with (self.root/OUTPUT).open() as f:
            row=next(csv.DictReader(f))
        self.assertEqual(row["Auditor_Page_1based"],"124")
        self.assertEqual(row["Consolidated_Balance_Sheet_Page_1based"],"125")
        self.assertEqual(row["Consolidated_Profit_Loss_Page_1based"],"126")
        self.assertEqual(row["Consolidated_Cash_Flow_Page_1based"],"127")
        self.assertEqual(row["Section_Review_State"],
                         "CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED")
        self.assertEqual(row["Audited_Consolidated_Facts_Verified"],"false")
        self.assertEqual(row["Core_Financial_Facts_Verified"],"0")
        self.assertLessEqual(int(row["Pages_Scanned"]),MAX_SCAN_PAGES)
        repeat=run(self.root,max_files=1,now=NOW,reader_cls=FakeReader)
        self.assertEqual(repeat["reports_reviewed_this_run"],0)
        self.assert_protected()

    def test_adaptive_scan_recovers_four_consecutive_official_sections_between_samples(self):
        # The old sparse 9-page stride entirely skipped pages 205-208 of
        # a 280-page annual report, despite those statements being present.
        self.assertFalse(set(range(205,209)) & set(page_indices(280)))
        self.assertTrue(set(range(205,209)).issubset(set(dense_financial_band(280))))
        report=run(self.root,max_files=1,now=NOW,reader_cls=FakeSparseGapReader)
        self.assertEqual(report["reports_reviewed_this_run"],1)
        with (self.root/OUTPUT).open() as handle:
            row=next(csv.DictReader(handle))
        self.assertEqual(row["Auditor_Page_1based"],"206")
        self.assertEqual(row["Consolidated_Balance_Sheet_Page_1based"],"207")
        self.assertEqual(row["Consolidated_Profit_Loss_Page_1based"],"208")
        self.assertEqual(row["Consolidated_Cash_Flow_Page_1based"],"209")
        self.assertEqual(row["Review_Version"],VERSION)
        self.assertLessEqual(int(row["Pages_Scanned"]),MAX_SCAN_PAGES)
        self.assertEqual(row["Audited_Consolidated_Facts_Verified"],"false")
        self.assertEqual(report["strict_annual_passes_added"],0)
        self.assert_protected()

    def test_old_page_locator_records_are_reexamined_for_same_exact_pdf_sha(self):
        # Changing review rules should not leave an old incorrect
        # MISSING_SECTION decision permanently cached in the review index.
        self.root.joinpath(OUTPUT).write_text(
            "Symbol,ISIN,Financial_Year,Official_Report_URL,PDF_SHA256,"
            "Review_Version,Section_Review_State\n"
            + f"{SYMBOL},{ISIN},2022-23,{URL},{self.receipt[0]['PDF_SHA256']},"
              "section-review-1,INCOMPLETE_SECTIONS_OR_FISCAL_YEAR_REQUIRES_REVIEW\n"
        )
        result=run(self.root,max_files=1,now=NOW,reader_cls=FakeSparseGapReader)
        self.assertEqual(result["reports_reviewed_this_run"],1)
        with (self.root/OUTPUT).open() as handle:
            rows=list(csv.DictReader(handle))
        self.assertEqual({r["Review_Version"] for r in rows},
                         {"section-review-1",VERSION})
        self.assert_protected()

    def test_old_review_is_preserved_but_current_view_excludes_stale_pages(self):
        # A former v2 false positive listed all four sections at page 200.
        # It must remain audit history while a separate CURRENT-version view
        # contains only verified-source v3 candidate page locators.
        header=(
            "Symbol,ISIN,Financial_Year,Official_Report_URL,PDF_SHA256,"
            "Auditor_Page_1based,Consolidated_Balance_Sheet_Page_1based,"
            "Consolidated_Profit_Loss_Page_1based,"
            "Consolidated_Cash_Flow_Page_1based,Review_Version,Reviewed_At_UTC"
        )
        old_row=(
            f"{SYMBOL},{ISIN},2022-23,{URL},{self.receipt[0]['PDF_SHA256']},"
            "200,200,200,200,section-review-2-adaptive,"
            "2026-10-17T09:55:00+00:00"
        )
        (self.root/OUTPUT).write_text(header+"\n"+old_row+"\n")
        report=run(self.root,max_files=1,now=NOW,reader_cls=FakeReader)
        self.assertEqual(report["reports_reviewed_this_run"],1)
        self.assertEqual(report["previous_review_records"],1)
        self.assertEqual(report["current_version_review_records"],1)
        self.assertEqual(report["latest_review_csv"],LATEST_OUTPUT)
        self.assertEqual(report["most_recent_current_review"]["physical_pdf_pages"],
                         {"auditor":124,"balance_sheet":125,
                          "profit_and_loss":126,"cash_flow":127})
        with (self.root/OUTPUT).open() as f:
            history=list(csv.DictReader(f))
        with (self.root/LATEST_OUTPUT).open() as f:
            current=list(csv.DictReader(f))
        self.assertEqual(len(history),2)
        self.assertEqual(len(current),1)
        self.assertEqual(current[0]["Review_Version"],VERSION)
        self.assertEqual(current[0]["Auditor_Page_1based"],"124")
        self.assertEqual(history[0]["Auditor_Page_1based"],"200")
        self.assertEqual(report["financial_facts_added"],0)
        self.assert_protected()

    def test_20microns_fy2324_separate_true_pages_not_section_index(self):
        # Reproduction of 20MICRONS official FY23/24 NSE PDF:
        # section INDEX at page 200 and actual statements at 201/209/210/211.
        self.receipt[0]["Financial_Year"]="2023-24"
        self.index[0]["Financial_Year"]="2023-24"
        csv_write(self.root/PDF_LOG,PDF_FIELDS,self.receipt)
        csv_write(self.root/PDF_INDEX,OUTPUT_FIELDS,self.index)
        # This mock keeps the original file URL solely to test source matching;
        # it does not fabricate NSE data or affect real archives.
        report=run(self.root,max_files=1,now=NOW,
                   reader_cls=FakeTwentyMicronsFinancialReader)
        self.assertEqual(report["reports_reviewed_this_run"],1)
        with (self.root/OUTPUT).open() as f:
            row=next(csv.DictReader(f))
        self.assertEqual([
            row["Auditor_Page_1based"],
            row["Consolidated_Balance_Sheet_Page_1based"],
            row["Consolidated_Profit_Loss_Page_1based"],
            row["Consolidated_Cash_Flow_Page_1based"],
        ],["201","209","210","211"])
        self.assertNotIn("200",[
            row["Auditor_Page_1based"],
            row["Consolidated_Balance_Sheet_Page_1based"],
            row["Consolidated_Profit_Loss_Page_1based"],
            row["Consolidated_Cash_Flow_Page_1based"],
        ])
        self.assertEqual(row["Section_Review_State"],
                         "CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED")
        self.assertLessEqual(int(row["Pages_Scanned"]),MAX_SCAN_PAGES)
        self.assertEqual(report["financial_facts_added"],0)
        self.assertEqual(report["strict_annual_passes_added"],0)
        self.assert_protected()

    def test_21stcenmgm_layout_long_auditor_and_ampersand_statement(self):
        # Original FY2023-24 report uses 10-page auditor section before
        # three consecutive consolidated statements on pages 126-128.
        # Section headings are corroborated, but amounts are NOT verified.
        self.receipt[0]["Financial_Year"]="2023-24"
        self.index[0]["Financial_Year"]="2023-24"
        csv_write(self.root/PDF_LOG,PDF_FIELDS,self.receipt)
        csv_write(self.root/PDF_INDEX,OUTPUT_FIELDS,self.index)
        report=run(self.root,max_files=1,now=NOW,
                   reader_cls=FakeLongConsolidatedAuditorReader)
        self.assertEqual(report["reports_reviewed_this_run"],1)
        with (self.root/LATEST_OUTPUT).open() as handle:
            row=next(csv.DictReader(handle))
        self.assertEqual([
            row["Auditor_Page_1based"],
            row["Consolidated_Balance_Sheet_Page_1based"],
            row["Consolidated_Profit_Loss_Page_1based"],
            row["Consolidated_Cash_Flow_Page_1based"],
        ],["118","126","127","128"])
        self.assertEqual(row["Review_Version"],VERSION)
        self.assertEqual(row["Section_Review_State"],
                         "CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED")
        self.assertLessEqual(int(row["Pages_Scanned"]),MAX_SCAN_PAGES)
        self.assertEqual(report["financial_facts_added"],0)
        self.assertEqual(report["strict_annual_passes_added"],0)
        self.assert_protected()

    def test_63moons_two_adjacent_consolidated_balance_pages_located(self):
        # NSE official FY2023-24 annual report: auditor at physical 169;
        # consolidated assets at 179, liabilities continuation at 180;
        # P&L at 181 and cash flow at 185.
        self.receipt[0]["Financial_Year"]="2023-24"
        self.index[0]["Financial_Year"]="2023-24"
        csv_write(self.root/PDF_LOG,PDF_FIELDS,self.receipt)
        csv_write(self.root/PDF_INDEX,OUTPUT_FIELDS,self.index)
        report=run(self.root,max_files=1,now=NOW,
                   reader_cls=FakeSplitConsolidatedBalanceReader)
        self.assertEqual(report["reports_reviewed_this_run"],1)
        with (self.root/LATEST_OUTPUT).open() as handle:
            row=next(csv.DictReader(handle))
        self.assertEqual(
            [row["Auditor_Page_1based"],
             row["Consolidated_Balance_Sheet_Page_1based"],
             row["Consolidated_Profit_Loss_Page_1based"],
             row["Consolidated_Cash_Flow_Page_1based"]],
            ["169","179","181","185"])
        self.assertEqual(row["Review_Version"],VERSION)
        self.assertEqual(row["Section_Review_State"],
                         "CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED")
        self.assertLessEqual(int(row["Pages_Scanned"]),MAX_SCAN_PAGES)
        self.assertEqual(report["financial_facts_added"],0)
        self.assertEqual(report["strict_annual_passes_added"],0)
        self.assertEqual(report["orders"],"DISABLED")
        self.assert_protected()

    def test_split_page_balance_totals_disagree_fails_closed(self):
        self.receipt[0]["Financial_Year"]="2023-24"
        self.index[0]["Financial_Year"]="2023-24"
        csv_write(self.root/PDF_LOG,PDF_FIELDS,self.receipt)
        csv_write(self.root/PDF_INDEX,OUTPUT_FIELDS,self.index)
        run(self.root,max_files=1,now=NOW,
            reader_cls=FakeSplitBalanceMismatchReader)
        with (self.root/LATEST_OUTPUT).open() as handle:
            row=next(csv.DictReader(handle))
        self.assertEqual(row["Consolidated_Balance_Sheet_Page_1based"],"")
        self.assertEqual(row["Section_Review_State"],
                         "INCOMPLETE_SECTIONS_OR_FISCAL_YEAR_REQUIRES_REVIEW")
        self.assert_protected()

    def test_contents_index_only_cannot_claim_four_financial_statements(self):
        self.receipt[0]["Financial_Year"]="2023-24"
        self.index[0]["Financial_Year"]="2023-24"
        csv_write(self.root/PDF_LOG,PDF_FIELDS,self.receipt)
        csv_write(self.root/PDF_INDEX,OUTPUT_FIELDS,self.index)
        run(self.root,max_files=1,now=NOW,reader_cls=FakeSectionIndexOnlyReader)
        with (self.root/OUTPUT).open() as f:
            row=next(csv.DictReader(f))
        self.assertEqual(row["Section_Review_State"],
                         "INCOMPLETE_SECTIONS_OR_FISCAL_YEAR_REQUIRES_REVIEW")
        self.assertEqual(row["Auditor_Page_1based"],"")
        self.assertEqual(row["Consolidated_Balance_Sheet_Page_1based"],"")
        self.assertEqual(row["Consolidated_Profit_Loss_Page_1based"],"")
        self.assertEqual(row["Consolidated_Cash_Flow_Page_1based"],"")
        self.assert_protected()

    def test_wrong_annual_year_cannot_satisfy_candidate_review(self):
        run(self.root,max_files=1,now=NOW,reader_cls=FakeWrongFY)
        with (self.root/OUTPUT).open() as f:
            record=next(csv.DictReader(f))
        self.assertEqual(record["Cover_Fiscal_Year_Candidate"],"false")
        self.assertEqual(record["Section_Review_State"],
                         "INCOMPLETE_SECTIONS_OR_FISCAL_YEAR_REQUIRES_REVIEW")
        self.assert_protected()

    def test_tampered_archived_pdf_must_block_no_misleading_completion(self):
        self.pdf.write_bytes(BODY+b"tampered")
        with self.assertRaisesRegex(SectionReviewBlocked,"SHA256_CHANGED"):
            run(self.root,max_files=1,now=NOW,reader_cls=FakeReader)
        self.assertFalse((self.root/OUTPUT).exists())
        self.assert_protected()

    def test_unexpected_receipt_path_rejected(self):
        self.receipt[0]["Cached_Private_Path"]="../unsafe.pdf"
        csv_write(self.root/PDF_LOG,PDF_FIELDS,self.receipt)
        with self.assertRaisesRegex(SectionReviewBlocked,"PATH_NOT_EXPECTED"):
            run(self.root,max_files=1,now=NOW,reader_cls=FakeReader)
        self.assert_protected()

    def test_wrong_issuer_isin_index_refused(self):
        self.index[0]["ISIN"]="INE999A01011"
        csv_write(self.root/PDF_INDEX,OUTPUT_FIELDS,self.index)
        with self.assertRaisesRegex(SectionReviewBlocked,"IDENTITY_OR_INDEX"):
            run(self.root,max_files=1,now=NOW,reader_cls=FakeReader)

    def test_pdf_not_available_does_not_claim_financial_success(self):
        self.pdf.unlink()
        with self.assertRaisesRegex(SectionReviewBlocked,"MISSING"):
            run(self.root,max_files=1,now=NOW,reader_cls=FakeReader)


if __name__=="__main__":
    unittest.main()
