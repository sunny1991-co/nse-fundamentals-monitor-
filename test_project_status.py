import csv
import json
import tempfile
import unittest
from pathlib import Path

from project_status import snapshot


class ProjectStatusTests(unittest.TestCase):
    def test_history_catchup_and_completed_research_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()

            (runtime / "nse_eod_history_status.json").write_text(json.dumps({
                "state": "BOUNDED_PROGRESS",
                "daily": {"remaining_archive_days": 123, "unverified_no_file_days": 0},
                "archive_gap_audit": {"unverified_expected_session_gaps": 0},
                "corporate_action_audit": {"unsupported_symbols": 7},
                "publish": {"published": False},
            }), encoding="utf-8")
            (runtime / "worker_status.json").write_text(json.dumps({
                "phase": "LIVE_MONITORING", "live_feed": "UPSTOX_DEFERRED",
                "sms": "PROVIDER_SETUP_REQUIRED",
            }), encoding="utf-8")
            (runtime / "history_worker_status.json").write_text(
                json.dumps({"state": "COLLECTING"}), encoding="utf-8"
            )

            with (root / "annual_fundamental_quality_summary.csv").open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "Financial_Year", "Year_Data_Quality_Pass",
                ])
                writer.writeheader()
                writer.writerow({"Financial_Year": "FUNDAMENTAL_SCREEN", "Year_Data_Quality_Pass": "0"})
            with (root / "final_fundamental_screen_summary.csv").open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["Final_Shortlist_Count"])
                writer.writeheader(); writer.writerow({"Final_Shortlist_Count": "0"})

            status = snapshot(root)
            self.assertEqual(status["stage"], "HISTORY_CATCHUP")
            self.assertEqual(status["remaining_archive_days"], 123)
            self.assertEqual(status["corporate_action_review_symbols"], 7)
            self.assertEqual(status["provisional_fast_fundamental_pass"], 0)
            self.assertEqual(status["provisional_fast_candidates"], 0)
            self.assertEqual(status["provisional_scope"], "NOT_RUN")

            history = json.loads((runtime / "nse_eod_history_status.json").read_text())
            history["state"] = "ADJUSTED_HISTORY_PUBLISHED"
            history["publish"] = {"published": True}
            history["daily"]["remaining_archive_days"] = 0
            (runtime / "nse_eod_history_status.json").write_text(
                json.dumps(history), encoding="utf-8"
            )
            (runtime / "history_backtest_done.json").write_text(json.dumps({
                "state": "BACKTEST_SCREEN_PAPER_READINESS_COMPLETE"
            }), encoding="utf-8")

            status = snapshot(root)
            self.assertEqual(
                status["stage"],
                "RESEARCH_READY_NO_STRICT_FUNDAMENTAL_CANDIDATES",
            )


if __name__ == "__main__":
    unittest.main()
