"""Synthetic forensic tests; no API, real broker tokens, or trading."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import tempfile
import unittest

from upstox_reference_forensics import (
    create_forensics, ForensicError, _hypotheses, _category
)
from upstox_reference_reconcile import METRICS
from upstox_reference_batches import _stored_paths
from test_upstox_reference_review import prepare


class ForensicTests(unittest.TestCase):
    def test_original_source_details_and_unverified_causes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            record,history,master,recon=prepare(root)
            originals=(record.read_bytes(),history.read_bytes(),master.read_bytes())
            result=create_forensics(root)
            self.assertEqual(result["upstox_companies_checked"],1)
            self.assertEqual(result["metric_discrepancies"],1)
            self.assertEqual(result["companies_needing_review"],1)
            self.assertEqual(result["source_review_severity_counts"],{
                "VERY_LARGE_VALUE_DIFFERENCE":1
            })
            self.assertEqual(result["disagreements_by_metric"],{
                "net_profit":1
            })
            self.assertEqual(result["disagreements_by_year"],{
                "2025-26":1
            })
            detail=result["top_discrepancies"][0]
            self.assertEqual(detail["symbol"],"SYNTHETIC")
            self.assertEqual(detail["financial_year"],"2025-26")
            self.assertEqual(detail["metric"],"net_profit")
            self.assertEqual(detail["upstox_vendor_crore"],60.)
            self.assertEqual(detail["verified_nse_crore"],25.)
            self.assertEqual(detail["difference_pct_of_larger_abs_value"],58.333)
            self.assertIn("UNEXPLAINED",detail["possible_explanations_not_verified"])
            self.assertEqual(result["live_orders"],"DISABLED")
            self.assertFalse(result["automatic_source_correction"])
            self.assertFalse(result["trading_eligibility"])
            path=_stored_paths(root)[0]/"reconciliation"/"source_discrepancy_forensics.csv"
            self.assertTrue(path.is_file())
            with path.open() as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),1)
            self.assertIn("nsearchives.nseindia.com",
                          rows[0]["nse_filing_url"])
            self.assertEqual(rows[0]["eligible_for_trading"],"False")
            self.assertEqual(
                (record.read_bytes(),history.read_bytes(),master.read_bytes()),
                originals
            )

    def test_source_mismatch_or_staleness_blocks_no_autofix(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            vendor,history,master,recon=prepare(root)
            history.write_text(history.read_text()+"\\n")
            with self.assertRaises(Exception):
                create_forensics(root)
            self.assertFalse(
                (_stored_paths(root)[0]/"reconciliation"/
                 "source_discrepancy_forensics.csv").exists()
            )

    def test_comparison_nse_values_conflicted_block_even_if_forged_checksum(self):
        with tempfile.TemporaryDirectory() as d:
            from hashlib import sha256
            root=Path(d)
            prepare(root)
            folder=_stored_paths(root)[0]/"reconciliation"
            path=folder/"metric_comparisons.csv"
            with path.open(newline="") as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),8)
            for item in rows:
                if item["status"]=="DIFFERENCE_REQUIRES_SOURCE_REVIEW":
                    item["verified_nse_crore"]="888"
            with path.open("w",newline="") as f:
                w=csv.DictWriter(f,fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            summary_path=folder/"summary.json"
            summary=json.loads(summary_path.read_text())
            summary["metric_comparisons_csv_sha256"]=sha256(
                path.read_bytes()
            ).hexdigest()
            summary_path.write_text(json.dumps(summary))
            with self.assertRaisesRegex(
                ForensicError,"ORIGINAL_NSE_NUMERIC_FACT_CHANGED"
            ):
                create_forensics(root)

    def test_period_year_ambiguity_is_only_possible_hypothesis(self):
        official={
            ("SYNTHETIC","2024-25"):{
                "Net_Profit_Cr":"60",
            },
            ("SYNTHETIC","2025-26"):{
                "Net_Profit_Cr":"25",
                "Profit_Before_Tax_Cr":"60",
            }
        }
        notes=_hypotheses(
            "net_profit","2025-26",60.,25.,
            official[("SYNTHETIC","2025-26")],official,
            "SYNTHETIC"
        )
        self.assertIn("POSSIBLE_PBT_VS_NET_PROFIT_DEFINITION",notes)
        self.assertIn("POSSIBLE_ADJACENT_FISCAL_YEAR_ALIGNMENT",notes)
        self.assertNotIn("VERIFIED",notes)
        self.assertEqual(
            _category(-1,2,150),"SIGN_OR_ZERO_DISAGREEMENT"
        )

    def test_sign_and_scale_hints_are_not_corrections(self):
        original={"Total_Income_Cr":"121"}
        hints=_hypotheses("revenue","2025-26",121,100,
                           original,{},"SYNTHETIC")
        self.assertIn(
            "POSSIBLE_TOTAL_INCOME_VS_REVENUE_DEFINITION",hints
        )
        hints=_hypotheses("operating_cash_flow","2025-26",-100.,100.,
                           {},{},"SYNTHETIC")
        self.assertIn(
            "POSSIBLE_CASH_OR_PROFIT_SIGN_CONVENTION",hints
        )
        hints=_hypotheses("revenue","2025-26",10000.,100.,
                           {},{},"SYNTHETIC")
        self.assertIn("POSSIBLE_FACTOR_OF_10_OR_100",hints)

    def test_forged_metrics_unverified_year_is_not_source_proof(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            prepare(root,verified=False)
            result=create_forensics(root)
            self.assertEqual(result["metric_discrepancies"],0)
            self.assertEqual(result["companies_needing_review"],0)
            self.assertEqual(result["nse_verified_years_added"],0)
            self.assertFalse(result["automatic_source_correction"])

    def test_strict_top_limit(self):
        with tempfile.TemporaryDirectory() as d:
            for n in [0,26,-1,1.5,True]:
                with self.assertRaises(ValueError):
                    create_forensics(d,top=n)


if __name__=="__main__":
    unittest.main()
