"""Offline project status must not promote Upstox reference matching to a trade pass."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from project_status import snapshot

SCOPE="NSE_XBRL_VS_UPSTOX_REFERENCE_RECONCILIATION_V1"


class ProjectVendorAuditTests(unittest.TestCase):
    def test_verified_source_comparisons_display_as_reference_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            output=(
                root/"runtime"/"experiments"/"upstox-nse-historical-reference-v1"
                /"upstox-reference-batches-v1"/"reconciliation"/"summary.json"
            )
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps({
                "scope":SCOPE,
                "metric_comparisons":42,
                "companies_needing_vendor_vs_nse_review":4,
                "nse_verified_years_added":0,
                "fundamental_strict_pass_changes":0,
                "groww_orders":"DISABLED",
            }))
            report=snapshot(root)
            self.assertEqual(report["upstox_vs_verified_nse_comparisons"],42)
            self.assertEqual(
                report["upstox_vs_nse_companies_needing_source_review"],4
            )
            self.assertTrue(
                report["upstox_reconciliation_is_offline_reference_only"]
            )
            self.assertEqual(report["annual_strict_pass"],0)
            self.assertEqual(report["final_fundamental_shortlist"],0)
            self.assertEqual(report["verified_profitable_strategies"],0)
            self.assertFalse(report["strategy_trading_enabled"])
            self.assertEqual(
                report["automatic_groww_order_execution"],
                "DISABLED_NO_VALIDATED_STRATEGY"
            )

    def test_wrong_report_scope_not_treated_as_source_quality(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            output=(
                root/"runtime"/"experiments"/"upstox-nse-historical-reference-v1"
                /"upstox-reference-batches-v1"/"reconciliation"/"summary.json"
            )
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps({
                "scope":"UNTRUSTED_EXTERNAL_REPORT",
                "metric_comparisons":999999,
                "companies_needing_vendor_vs_nse_review":5000
            }))
            report=snapshot(root)
            self.assertEqual(report["upstox_vs_verified_nse_comparisons"],0)
            self.assertEqual(
                report["upstox_vs_nse_companies_needing_source_review"],0
            )


if __name__=="__main__":
    unittest.main()
