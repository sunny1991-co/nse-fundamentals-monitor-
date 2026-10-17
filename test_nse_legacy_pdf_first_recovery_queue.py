"""Offline NSE mainboard older audited Annual Report PDF source recovery tests."""
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import csv
import unittest

from nse_legacy_pdf_first_recovery_queue import (
    snapshot,OUTPUT,STATUS,PDFRecoveryBlocked,stage_next
)
from nse_legacy_annual_evidence_queue import OUTPUT as SOURCE_QUEUE
from nse_mainboard_discovery import CATALOG

NOW=datetime(2026,10,17,14,tzinfo=timezone.utc)
ISSUERS=[
    ("20MICRONS","INE144J01027",True),
    ("BPL","INE110A01019",True),
    ("BRITANNIA","INE216A01030",True),
    ("NEWCO","INE111B01011",False),
]

def csv_data(path,fields,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as handle:
        w=csv.DictWriter(handle,fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


class PDFSourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder=TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root=Path(folder.name)
        catalog=[
            {"Symbol":sym,"ISIN":isin,"Company":sym+" Limited",
             "Baseline_Tracked":"true" if tracked else "false",
             "Series":"EQ"}
            for sym,isin,tracked in ISSUERS
        ]
        csv_data(self.root/CATALOG,list(catalog[0]),catalog)
        self.source_rows=[]
        for sym,isin,_ in ISSUERS:
            for fy in ("2022-23","2023-24"):
                self.source_rows.append({
                    "Symbol":sym,"ISIN":isin,"Financial_Year":fy,
                    "Source_Stage":"NO_LOCAL_LEGACY_SOURCE_INDEX_YET",
                    "Independent_PDF_Evidence_Stage":"NO_SEPARATE_PDF_EVIDENCE",
                    "Official_PDF_Index_Links":"0",
                    "Official_Cached_PDF_Source_Receipts":"0",
                    "Previous_FY_Comparative_Six_Core_From_Later_PDF":"false",
                })
        self.set_evidence(
            "20MICRONS","2023-24",
            stage="CURRENT_FY_SIX_PDF_CORE_CANDIDATES_UNVERIFIED",
            source_stage="ORIGINAL_XBRL_INDEX_LINKS_ONLY")
        self.set_evidence(
            "20MICRONS","2022-23",
            stage="LATER_YEAR_REPORT_PRIOR_FY_COMPARATIVE_ONLY",
            source_stage="ORIGINAL_XBRL_INDEX_LINKS_ONLY",
            comparative="true")
        self.set_evidence("BPL","2023-24",
                          stage="OFFICIAL_NSE_PDF_URL_INDEXED",
                          source_stage="OFFICIAL_PDF_INDEX_LINKS_ONLY",
                          links="1")
        self.write_sources()
        csv_data(self.root/"runtime/nse_official_annual_pdf_index_ledger.csv",
                 ["Symbol","Status","Checked_At_UTC"],
                 [{"Symbol":"BRITANNIA",
                   "Status":"OFFICIAL_METADATA_RECEIVED",
                   "Checked_At_UTC":"2026-10-16T08:00:00+00:00"}])
        self.patch_queue=patch(
            "nse_legacy_pdf_first_recovery_queue.build_source_queue",
            return_value={"official_mainboard_eq_companies":4},
        )
        self.patch_queue.start()
        self.addCleanup(self.patch_queue.stop)
        self.patch_baseline=patch(
            "nse_legacy_pdf_first_recovery_queue.baseline_coverage",
            return_value=(
                {"20MICRONS":2,"BPL":2,"BRITANNIA":2,"NEWCO":0},
                {"20MICRONS":2,"BPL":2,"BRITANNIA":2,"NEWCO":0},
            ))
        self.patch_baseline.start()
        self.addCleanup(self.patch_baseline.stop)
        self.patch_verify=patch(
            "nse_legacy_pdf_first_recovery_queue.verified_legacy_years",
            return_value=set())
        self.patch_verify.start()
        self.addCleanup(self.patch_verify.stop)
        self.patch_cooldown=patch(
            "nse_legacy_pdf_first_recovery_queue.source_access_cooldown",
            return_value="2026-10-18T06:44:22+00:00")
        self.patch_cooldown.start()
        self.addCleanup(self.patch_cooldown.stop)
        self.sensitive={
            "annual_historical_results.csv":b"SOURCE_VERIFIED_HISTORY_PRESERVE",
            "final_fundamental_shortlist.csv":b"STRICT_FOUR_YEAR_FILTER_PRESERVE",
            "swing_watchlist.json":b"BROKER_TRADING_DISABLED",
        }
        for path,contents in self.sensitive.items():
            (self.root/path).write_bytes(contents)

    def set_evidence(self,sym,fy,*,stage,source_stage,links="0",comparative="false"):
        row=next(r for r in self.source_rows
                 if r["Symbol"]==sym and r["Financial_Year"]==fy)
        row.update({
            "Independent_PDF_Evidence_Stage":stage,
            "Source_Stage":source_stage,
            "Official_PDF_Index_Links":links,
            "Previous_FY_Comparative_Six_Core_From_Later_PDF":comparative,
        })

    def write_sources(self):
        csv_data(self.root/SOURCE_QUEUE,list(self.source_rows[0]),self.source_rows)

    def data(self):
        with (self.root/OUTPUT).open(newline="") as handle:
            return list(csv.DictReader(handle))

    def assert_sensitive_unchanged(self):
        for path,contents in self.sensitive.items():
            self.assertEqual((self.root/path).read_bytes(),contents)

    def test_offline_full_mainboard_pdf_priority_preserves_two_source_lanes(self):
        result=snapshot(self.root,now=NOW)
        self.assertEqual(result["current_attested_nse_mainboard_eq_companies"],4)
        self.assertEqual(result["official_nse_eq_company_years_considered"],8)
        self.assertEqual(result["network_requests_this_run"],0)
        self.assertEqual(result["new_audited_xbrl_facts_verified"],0)
        self.assertEqual(result["strict_fundamental_passes_added"],0)
        self.assertEqual(result["orders"],"DISABLED")
        self.assertEqual(result[
            "previous_official_nse_annual_pdf_api_cooldown_until_utc"],
            "2026-10-18T06:44:22+00:00")
        rows=self.data()
        self.assertEqual([r["Symbol"] for r in rows],
                         ["BPL","20MICRONS","BRITANNIA","NEWCO"])
        by_sym={r["Symbol"]:r for r in rows}
        self.assertEqual(by_sym["BPL"]["Next_Research_Action"],
                         "ARCHIVE_ALREADY_INDEXED_OFFICIAL_FY2023_24_PDF_WHEN_SOURCE_PERMITTED")
        self.assertEqual(by_sym["20MICRONS"]["Next_Research_Action"],
                         "BOTH_OLD_FYS_PDF_RESEARCH_ONLY_SEPARATE_AUDITED_XML_STILL_REQUIRED")
        self.assertEqual(by_sym["20MICRONS"]["FY2022_23_From_Later_PDF_Only_Not_Original_XBRL"],
                         "true")
        self.assertEqual(by_sym["20MICRONS"][
            "FY2023_24_Separate_PDF_Evidence_Stage"],
            "CURRENT_FY_SIX_PDF_CORE_CANDIDATES_UNVERIFIED")
        self.assertEqual(by_sym["BRITANNIA"]["Next_Research_Action"],
                         "REVIEW_OTHER_OFFICIAL_ANNUAL_REPORT_ARCHIVE_SOURCE_NO_PRIOR_OLD_FY_PDF_LINK")
        self.assertEqual(by_sym["NEWCO"]["Baseline_Tracked"],"false")
        self.assertEqual(by_sym["NEWCO"]["Two_Newer_FYs_Six_Core_Row_Count_Not_Four_Year_Pass"],"0")
        self.assertTrue(all(r["Trade_Eligible"]=="false" for r in rows))
        self.assertTrue(all(r["Orders"]=="DISABLED" for r in rows))
        self.assert_sensitive_unchanged()

    def test_tampered_mainboard_isin_rejects_whole_source_plan(self):
        original=self.root/OUTPUT
        snapshot(self.root,now=NOW)
        before=original.read_bytes()
        self.source_rows[0]["ISIN"]="INE000000000"
        self.write_sources()
        with self.assertRaisesRegex(PDFRecoveryBlocked,"ISIN"):
            snapshot(self.root,now=NOW)
        self.assertEqual(original.read_bytes(),before)
        self.assert_sensitive_unchanged()

    def test_missing_or_duplicate_company_year_blocks(self):
        self.source_rows.append(dict(self.source_rows[0]))
        self.write_sources()
        with self.assertRaisesRegex(PDFRecoveryBlocked,"SOURCE_QUEUE_INCOMPLETE"):
            snapshot(self.root,now=NOW)
        self.assertFalse((self.root/OUTPUT).exists())
        self.assert_sensitive_unchanged()

    def test_verified_prior_source_does_not_assume_other_year_verified(self):
        self.patch_verify.stop()
        with patch(
            "nse_legacy_pdf_first_recovery_queue.verified_legacy_years",
            return_value={("20MICRONS","2022-23")}):
            snapshot(self.root,now=NOW)
        data={r["Symbol"]:r for r in self.data()}
        self.assertEqual(data["20MICRONS"][
            "FY2022_23_Original_XBRL_Already_Verified_Locally"],"true")
        self.assertEqual(data["20MICRONS"][
            "FY2023_24_Original_XBRL_Already_Verified_Locally"],"false")
        self.assertEqual(data["20MICRONS"]["Trade_Eligible"],"false")
        self.assert_sensitive_unchanged()

    def test_source_actions_do_not_claim_pdf_comparative_is_xbrl(self):
        fake={
            "Independent_PDF_Evidence_Stage":"LATER_YEAR_REPORT_PRIOR_FY_COMPARATIVE_ONLY"
        }
        later={"Independent_PDF_Evidence_Stage":
               "CURRENT_FY_SIX_PDF_CORE_CANDIDATES_UNVERIFIED"}
        rank,action=stage_next(fake,later,False,False,{})
        self.assertEqual(rank,5)
        self.assertIn("PDF_RESEARCH_ONLY",action)
        self.assertNotIn("VERIFIED",action)
        rank,action=stage_next(fake,later,True,True,{})
        self.assertEqual(rank,7)
        self.assertIn("REVIEW_STRICT",action)


if __name__=="__main__":
    unittest.main()
