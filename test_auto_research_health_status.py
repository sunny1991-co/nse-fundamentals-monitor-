"""Status must distinguish raw new-company backfill from adjusted legacy EOD."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from project_status import snapshot


class AutoResearchHealthStatusTests(unittest.TestCase):
    def test_automated_pipeline_status_is_separate_and_never_trade_permission(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            runtime=root/"runtime"
            runtime.mkdir()
            reports={
                "nse_eod_history_status.json":{
                    "state":"ADJUSTED_HISTORY_PUBLISHED",
                    "publish":{"published":True},
                    "daily":{"remaining_archive_days":0},
                },
                "nse_mainboard_discovery_status.json":{
                    "state":"OFFICIAL_DISCOVERY_UPDATED",
                    "mainboard_eq_count":2344,"outside_annual_baseline":747,
                },
                "mainboard_raw_research_history_status.json":{
                    "state":"RESEARCH_UNADJUSTED_EOD_PROGRESS",
                    "all_complete_archive_days":5,
                    "estimated_outstanding_expected_sessions":1681,
                    "source_blocked":False,
                },
                "nse_mainboard_auto_research_radar_status.json":{
                    "state":"NSE_MAINBOARD_AUTO_RESEARCH_RADAR_UPDATED",
                    "unadjusted_breakout_research_candidates":4,
                },
                "nse_official_annual_pdf_collector_status.json":{
                    "state":"OFFICIAL_ANNUAL_REPORT_METADATA_CAPTURED",
                    "indexed_company_total":3,
                    "total_candidate_pdf_index_rows":2,
                },
                "nse_totalmarket_industry_reference_status.json":{
                    "state":"OFFICIAL_COARSE_REFERENCE_UPDATED",
                    "reference_rows_this_run":750,
                }
            }
            for name,value in reports.items():
                (runtime/name).write_text(json.dumps(value),encoding="utf-8")
            status=snapshot(root)
            self.assertEqual(status["remaining_archive_days"],0)
            self.assertEqual(status["automatic_mainboard_eq_members"],2344)
            self.assertEqual(status["automatic_new_research_only_symbols"],747)
            self.assertEqual(status["automatic_mainboard_unadjusted_archive_days_complete"],5)
            self.assertEqual(status["automatic_mainboard_unadjusted_archive_days_pending"],1681)
            self.assertEqual(status["automatic_mainboard_raw_breakout_candidates"],4)
            self.assertFalse(status["automatic_mainboard_radar_is_tradable"])
            self.assertEqual(status["automatic_official_annual_pdf_candidates"],2)
            self.assertFalse(status["official_coarse_sector_reference_is_full_4_tier"])
            self.assertEqual(status["annual_strict_pass"],0)
            self.assertEqual(status["orders"],"MANUAL_ONLY")

    def test_missing_new_pipeline_status_is_not_fake_success(self):
        with TemporaryDirectory() as d:
            status=snapshot(Path(d))
            self.assertEqual(status["automatic_mainboard_raw_backfill_state"],"NOT_STARTED")
            self.assertEqual(status["automatic_mainboard_unadjusted_archive_days_complete"],0)
            self.assertEqual(status["automatic_mainboard_radar_state"],"NOT_STARTED")
            self.assertEqual(status["automatic_official_annual_pdf_index_state"],"NOT_STARTED")
            self.assertFalse(status["automatic_mainboard_radar_is_tradable"])


if __name__=="__main__":
    unittest.main()
