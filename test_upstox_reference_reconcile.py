"""Synthetic read-only Upstox-vs-NSE provenance and numeric reconciliation tests."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from upstox_reference_reconcile import (
    _compare, _verified, _official_history, ReconciliationError,
    reconcile,
)
from upstox_reference_batches import (
    _checkpoint_one, _stored_paths, ReferenceBatchError,
)

SYMBOL="SYNTHETIC"
ISIN="INE002A01018"
TOKEN="SECRET_SHOULD_NEVER_APPEAR"
SRC="https://nsearchives.nseindia.com/corporate/xbrl/"
BASE=SRC+"INTEGRATED_FILING_INDAS_TEST_16052026120000_WEB.xml"
OTHER=SRC+"INTEGRATED_FILING_INDAS_TEST_17052026120000_WEB.xml"


def original_row(year,*,url=BASE, verified=True, profit="25",
                 status="VERIFIED",published="2026-05-16",
                 basis="Consolidated", cash="20"):
    return {
        "Symbol":SYMBOL,
        "Financial_Year":year,
        "Fiscal_Year_End":{
            "2024-25":"2025-03-31", "2025-26":"2026-03-31"
        }[year],
        "Reporting_Basis":basis,
        "Audit_Status":"Audited",
        "NSE_Consolidation_Verified":str(verified),
        "Source_URL":url,
        "Filing_Published_At":published,
        "Status":status,
        "Revenue_Operations_Cr":"100",
        "Net_Profit_Cr":profit,
        "Total_Assets_Cr":"200",
        "Cash_Flow_Operating_Cr":cash,
    }


def write_history(root,rows):
    path=Path(root)/"annual_historical_results.csv"
    with path.open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(original_row("2024-25")))
        writer.writeheader()
        writer.writerows(rows)
    return path


def vendor_row():
    return {
        "symbol":SYMBOL,
        "isin":ISIN,
        "status":"UPSTOX_REFERENCE_ONLY_NOT_NSE_VERIFIED",
        "report_basis":"UPSTOX_API_ASSERTS_CONSOLIDATED_NOT_XBRL_VERIFIED",
        "is_nse_audited_verified":False,
        "nse_sector_verified":False,
        "vendor_sector_reference":"Engineering",
        "financials":{
            "Mar 2025":{
                "income_revenue_vendor_crore":101.,
                "income_net_profit_vendor_crore":25.,
                "balance_total_assets_vendor_crore":200.,
                "operating_cash_flow_vendor_crore":20.,
            },
            "Mar 2026":{
                "income_revenue_vendor_crore":101.,
                "income_net_profit_vendor_crore":60.,
                "balance_total_assets_vendor_crore":200.,
                "operating_cash_flow_vendor_crore":20.,
            },
        },
    }


def plant_vendor(root):
    folder,records,_last=_stored_paths(root)
    records.mkdir(parents=True)
    mapping={SYMBOL:ISIN}
    return _checkpoint_one(records,mapping,SYMBOL,vendor_row())


class ReconTests(unittest.TestCase):
    def mapping(self,root):
        return {SYMBOL:ISIN},"a"*64

    def test_only_verified_original_and_flag_real_discrepancy(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            vendor=plant_vendor(root)
            original=write_history(root,[
                original_row("2024-25",profit="25"),
                original_row("2025-26",profit="25",cash=""),
            ])
            preserved=original.read_bytes()
            vendor_file=next(root.rglob("records/*.json"))
            vendor_preserved=vendor_file.read_bytes()
            report=reconcile(root,mapping_loader=self.mapping)
            self.assertEqual(report["vendor_company_records_checked"],1)
            self.assertEqual(report["verified_symbol_years"],2)
            self.assertEqual(report["metric_comparisons"],8)
            self.assertEqual(
                report["metric_status_counts"]["WITHIN_TOLERANCE_VENDOR_REFERENCE"],6
            )
            self.assertEqual(
                report["metric_status_counts"]["DIFFERENCE_REQUIRES_SOURCE_REVIEW"],1
            )
            self.assertEqual(
                report["metric_status_counts"]["VERIFIED_NSE_METRIC_MISSING"],1
            )
            self.assertEqual(report["companies_needing_vendor_vs_nse_review"],1)
            self.assertEqual(report["nse_verified_years_added"],0)
            self.assertEqual(report["official_nse_sector_rows_added"],0)
            self.assertEqual(report["fundamental_strict_pass_changes"],0)
            self.assertFalse(report["vendor_values_promoted_to_nse"])
            self.assertEqual(report["groww_orders"],"DISABLED")
            self.assertNotIn(TOKEN,json.dumps(report))
            self.assertEqual(original.read_bytes(),preserved)
            self.assertEqual(vendor_file.read_bytes(),vendor_preserved)
            report_path=(_stored_paths(root)[0]/
                         "reconciliation"/"summary.json")
            self.assertTrue(report_path.is_file())
            recorded=json.loads(report_path.read_text())
            self.assertEqual(
                recorded["nse_source_file_sha256"],
                report["nse_source_file_sha256"]
            )
            with (report_path.parent/"metric_comparisons.csv").open() as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),8)
            self.assertIn(
                "DIFFERENCE_REQUIRES_SOURCE_REVIEW",
                [r["status"] for r in rows]
            )

    def test_baseline_only_never_promotes_source_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            plant_vendor(root)
            write_history(root,[
                original_row("2024-25",verified=False),
                original_row("2025-26",basis="Standalone"),
            ])
            report=reconcile(root,mapping_loader=self.mapping)
            self.assertEqual(report["verified_symbol_years"],0)
            self.assertEqual(report["metric_comparisons"],0)
            self.assertEqual(
                report["metric_status_counts"]["NO_INDEPENDENT_VERIFIED_NSE_YEAR"],8
            )
            self.assertEqual(
                report["companies_with_a_comparable_nse_metric"],0
            )
            self.assertEqual(report["fundamental_strict_pass_changes"],0)

    def test_duplicate_same_day_conflicting_nse_revision_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            plant_vendor(root)
            write_history(root,[
                original_row("2024-25",url=BASE,published="2025-05-16"),
                original_row("2024-25",url=OTHER,published="2025-05-16"),
                original_row("2025-26",profit="25",published="2026-05-16"),
            ])
            report=reconcile(root,mapping_loader=self.mapping)
            self.assertEqual(report["ambiguous_revision_symbol_years"],1)
            self.assertEqual(report["verified_symbol_years"],1)
            self.assertEqual(report["metric_comparisons"],4)
            self.assertEqual(
                report["metric_status_counts"]["NO_INDEPENDENT_VERIFIED_NSE_YEAR"],4
            )

    def test_latest_revision_used_if_published_later(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            write_history(root,[
                original_row("2024-25",published="2025-05-16",profit="20"),
                original_row("2024-25",url=OTHER,
                             published="2026-05-17",profit="25"),
            ])
            selected,stats=_official_history(root)
            self.assertEqual(stats["verified_symbol_years"],1)
            self.assertEqual(
                selected[(SYMBOL,"2024-25")]["Net_Profit_Cr"],"25"
            )

    def test_missing_original_never_falsely_verifies_vendor(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            plant_vendor(root)
            report=reconcile(root,mapping_loader=self.mapping)
            self.assertEqual(report["verified_symbol_years"],0)
            self.assertEqual(report["metric_comparisons"],0)
            self.assertEqual(
                report["history_state"],
                "NO_ORIGINAL_VERIFIED_NSE_HISTORY_FILE"
            )

    def test_conflicting_original_facts_same_day_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            write_history(root,[
                original_row("2024-25",profit="20"),
                original_row("2024-25",profit="99"),
            ])
            selected,counts=_official_history(root)
            self.assertEqual(counts["ambiguous_revision_symbol_years"],1)
            self.assertEqual(selected,{})

    def test_nan_bool_negative_and_small_crore_tolerance(self):
        self.assertEqual(_compare(True,20)[0],"VENDOR_METRIC_MISSING")
        self.assertEqual(_compare(float("inf"),20)[0],"VENDOR_METRIC_MISSING")
        self.assertEqual(_compare(0.,0.)[0],
                         "WITHIN_TOLERANCE_VENDOR_REFERENCE")
        self.assertEqual(_compare(0.01,0.1)[0],
                         "WITHIN_TOLERANCE_VENDOR_REFERENCE")
        self.assertEqual(_compare(-10,-20)[0],
                         "DIFFERENCE_REQUIRES_SOURCE_REVIEW")
        self.assertEqual(_compare(100,106)[0],
                         "DIFFERENCE_REQUIRES_SOURCE_REVIEW")

    def test_no_untrusted_file_domains_or_standalone_promotion(self):
        valid=original_row("2024-25")
        self.assertTrue(_verified(valid))
        for fake in [
            original_row("2024-25",url="https://evil.example.com/paper.xml"),
            original_row("2024-25",
                         url="https://nsearchives.nseindia.com.evil.com/corporate/xbrl/test.xml"),
            original_row("2024-25",verified=False),
            original_row("2024-25",basis="Standalone"),
            original_row("2024-25",status="UNVERIFIED"),
        ]:
            self.assertFalse(_verified(fake))

    def test_corrupted_saved_reference_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            plant_vendor(root)
            file=next(root.rglob("records/*.json"))
            content=json.loads(file.read_text())
            content["is_nse_audited_verified"]=True
            file.write_text(json.dumps(content))
            with self.assertRaises(ReferenceBatchError):
                reconcile(root,mapping_loader=self.mapping)

    def test_bad_history_schema_blocks_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            plant_vendor(root)
            (root/"annual_historical_results.csv").write_text(
                "Symbol,Revenue_Operations_Cr\nSYNTHETIC,100\n"
            )
            with self.assertRaisesRegex(ReconciliationError,"PROVENANCE_FIELDS"):
                reconcile(root,mapping_loader=self.mapping)


if __name__=="__main__":
    unittest.main()
