"""Synthetic read-only NSE-vs-Upstox source-review priority/provenance tests."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from research_monitor.upstox_instrument_master import (
    MAP_NAME, METADATA_NAME, MASTER_URL,
)
from upstox_reference_batches import _source_dir, _stored_paths
from upstox_reference_reconcile import reconcile
from upstox_reference_review import (
    REVIEW_SCOPE, SourceReviewError, create_review,
)
from test_upstox_reference_reconcile import (
    SYMBOL,ISIN, plant_vendor, original_row, write_history, TOKEN,
)


def prepare(root, *, nse_profit="25", sector="Engineering", verified=True):
    root=Path(root)
    (root/"FY2425.csv").write_text("Symbol\n"+SYMBOL+"\n")
    (root/"NSE_FY2025-26_Consolidated_Fundamentals.csv").write_text(
        "Symbol\n"+SYMBOL+"\n"
    )
    base=_source_dir(root)
    base.mkdir(parents=True,exist_ok=True)
    vendor_map=base/MAP_NAME
    vendor_map.write_text(
        "SYMBOL,ISIN,SERIES,SOURCE\n"
        +SYMBOL+","+ISIN+",EQ,UPSTOX_NSE_BOD_CURRENT_VENDOR_MAP\n"
    )
    metadata={
        "scope":"CURRENT_NSE_EQ_VENDOR_CROSSWALK_ONLY_NOT_MAINBOARD_ATTESTATION",
        "source_url":MASTER_URL,
        "source_sha256":"a"*64,
        "data_source":"UPSTOX_PUBLIC_NSE_BOD_JSON_GZIP",
        "verified_mainboard_history":False,
        "nse_xbrl_verified":False,
        "fetched_at_utc":datetime.now(timezone.utc).isoformat(),
        "mapping_path":str(vendor_map),
        "verified_by_vendor_crosswalk":1,
    }
    (base/METADATA_NAME).write_text(json.dumps(metadata))
    vendor=plant_vendor(root)
    vendor_file=next(root.rglob("records/*.json"))
    vendor["vendor_sector_reference"]=sector
    vendor_file.write_text(json.dumps(vendor))
    history=write_history(root,[
        original_row("2024-25", verified=verified),
        original_row("2025-26",profit=nse_profit,verified=verified),
    ])
    report=reconcile(root)
    return vendor_file,history, vendor_map, report


class ReviewTests(unittest.TestCase):
    def test_one_high_impact_difference_creates_company_source_review(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            record,official,master,recon=prepare(root)
            before=(record.read_bytes(),official.read_bytes(),master.read_bytes())
            result=create_review(root)
            self.assertEqual(result["scope"],REVIEW_SCOPE)
            self.assertEqual(result["vendor_companies_in_reconciliation"],1)
            self.assertEqual(result["companies_requiring_source_review"],1)
            self.assertEqual(result["mismatched_financial_metrics"],1)
            self.assertEqual(result["compared_numeric_metrics_within_tolerance"],7)
            self.assertEqual(
                result["discrepancies_by_metric"],{"net_profit":1}
            )
            self.assertEqual(
                result["discrepancies_by_year"],{"2025-26":1}
            )
            self.assertEqual(
                result["source_review_priorities"],
                {"DIFFERENCE_AT_LEAST_20_PERCENT":1}
            )
            self.assertEqual(result["strict_fundamental_pass_changes"],0)
            self.assertEqual(result["live_orders"],"DISABLED")
            self.assertNotIn(TOKEN,json.dumps(result))
            path=_stored_paths(root)[0]/"reconciliation"/"company_source_review_queue.csv"
            with path.open() as file:
                rows=list(csv.DictReader(file))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["symbol"],SYMBOL)
            self.assertIn("2025-26:net_profit:",rows[0]["difference_details"])
            self.assertEqual(rows[0]["strict_fundamental_pass"],"False")
            self.assertEqual(rows[0]["trading_eligible"],"False")
            self.assertEqual(
                (record.read_bytes(),official.read_bytes(),master.read_bytes()),
                before
            )

    def test_sign_flip_is_urgently_reviewed_not_marked_as_bad_stock(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            prepare(root,nse_profit="-25",sector="Bank")
            result=create_review(root)
            self.assertEqual(result["source_review_priorities"],{
                "SIGN_OR_ZERO_MISMATCH":1
            })
            self.assertEqual(
                result["specialist_sector_companies_requiring_accounting_review"],1
            )
            self.assertTrue(result["source_data_review_only"])
            self.assertEqual(result["independent_nse_audited_verification_added"],0)

    def test_source_changed_after_reconciliation_requires_reaudit(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            _,history,_,_=prepare(root)
            history.write_text(history.read_text()+"\n")
            with self.assertRaisesRegex(
                SourceReviewError,"VERIFIED_NSE_SOURCE_CHANGED"
            ):
                create_review(root)

    def test_isin_map_changed_after_reconciliation_requires_reaudit(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            _,_,mapping,_=prepare(root)
            mapping.write_text(mapping.read_text()+"\n")
            with self.assertRaisesRegex(
                SourceReviewError,"VENDOR_ISIN_MAPPING_CHANGED"
            ):
                create_review(root)

    def test_vendor_value_changed_after_reconciliation_requires_reaudit(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            record,_,_,_=prepare(root)
            value=json.loads(record.read_text())
            value["financials"]["Mar 2026"]["income_net_profit_vendor_crore"]=64.
            record.write_text(json.dumps(value))
            with self.assertRaisesRegex(
                SourceReviewError,"VENDOR_RECORD_CHANGED"
            ):
                create_review(root)

    def test_corrupted_report_counts_do_not_create_queue(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            prepare(root)
            folder=_stored_paths(root)[0]/"reconciliation"
            summary=folder/"summary.json"
            report=json.loads(summary.read_text())
            report["metric_comparisons"]+=1
            summary.write_text(json.dumps(report))
            with self.assertRaisesRegex(
                SourceReviewError,"RECONCILIATION_COUNT_CHANGED"
            ):
                create_review(root)
            self.assertFalse((folder/"company_source_review_queue.csv").exists())

    def test_unverified_original_produces_empty_review_no_false_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            prepare(root,verified=False)
            result=create_review(root)
            self.assertEqual(result["companies_requiring_source_review"],0)
            self.assertEqual(result["mismatched_financial_metrics"],0)
            self.assertEqual(result["compared_numeric_metrics_within_tolerance"],0)
            self.assertEqual(result["strict_fundamental_pass_changes"],0)
            queue=_stored_paths(root)[0]/"reconciliation"/"company_source_review_queue.csv"
            with queue.open() as file:
                rows=list(csv.DictReader(file))
            self.assertEqual(rows,[])

    def test_duplicate_comparison_key_not_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            prepare(root)
            folder=_stored_paths(root)[0]/"reconciliation"
            path=folder/"metric_comparisons.csv"
            data=path.read_text()
            header,first,*rest=data.splitlines()
            with path.open("a") as f:
                f.write(first+"\n")
            summary=folder/"summary.json"
            report=json.loads(summary.read_text())
            report["metric_comparisons"]+=1
            summary.write_text(json.dumps(report))
            with self.assertRaisesRegex(
                SourceReviewError,"DUPLICATE_COMPARISON_METRIC"
            ):
                create_review(root)

if __name__=="__main__":
    unittest.main()
