"""No seven-strategy rank can surface from stale or mocked-private inputs."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from project_status import CODE_ROOT, snapshot


FILES = (
    "swing_seven_strategy_research.py",
    "research_monitor/seven_strategy_rules.py",
    "research_monitor/strategy_sources.py",
    "research_monitor/exploratory_ranking.py",
    "swing_backtest.py", "swing_strategy.py", "swing_pullback_research.py",
)


class SevenStrategyStatusTests(unittest.TestCase):
    def make_report(self, root):
        implementation = hashlib.sha256(b"".join(
            (CODE_ROOT / name).read_bytes() for name in FILES
        )).hexdigest()
        path = root / "runtime" / "experiments" / "seven-strategies-v1" / "comparison.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "scope": "SEVEN_HYPOTHESES_EXPLORATORY_ONLY",
            "data_sha256": "synthetic",
            "implementation_sha256": implementation,
            "validation_status": "NOT_VALIDATED",
            "live_eligible": False,
            "verified_top_three": [],
            "source_hashes": {}, "metadata_hashes": {},
            "summary": {"ema-pullback-v1": {}},
            "research_top_three_unvalidated": ["ema-pullback-v1"],
        }), encoding="utf-8")

    def test_missing_and_fresh_report_never_approves_live_trading(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(snapshot(root)["seven_strategy_research"], "NOT_RUN")
            self.make_report(root)
            self.assertEqual(
                snapshot(root)["seven_strategy_research"],
                "STALE_OR_UNVERIFIED_RESEARCH_REPORT",
            )
            (root / "market_data_metadata.json").write_text(
                json.dumps({"sha256": "synthetic"}), encoding="utf-8"
            )
            status = snapshot(root)
            self.assertEqual(status["seven_strategy_research"],
                             "CURRENT_EXPLORATORY_NOT_VALIDATED")
            self.assertEqual(status["seven_strategy_research_top_three"],
                             ["ema-pullback-v1"])
            self.assertEqual(status["seven_strategy_verified_top_three"], [])
            self.assertEqual(status["verified_profitable_strategies"], 0)
            self.assertFalse(status["strategy_trading_enabled"])
            (root / "nifty50_tri_history.csv").write_text(
                "Date,Close\n2024-01-01,20000\n", encoding="utf-8"
            )
            self.assertEqual(
                snapshot(root)["seven_strategy_research"],
                "STALE_OR_UNVERIFIED_RESEARCH_REPORT",
            )
            self.assertEqual(snapshot(root)["seven_strategy_research_top_three"], [])

    def test_tampered_metadata_invalidates_attested_benchmark(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.make_report(root)
            (root/"market_data_metadata.json").write_text(
                json.dumps({"sha256": "synthetic"}), encoding="utf-8"
            )
            csv_path = root / "nifty50_tri_history.csv"
            meta_path = root / "nifty50_tri_history_metadata.json"
            csv_path.write_text("Date,Close\n2024-02-02,22000\n")
            meta_path.write_text('{"operator_source_checked":true}')
            report_path = (
                root / "runtime" / "experiments"
                / "seven-strategies-v1" / "comparison.json"
            )
            report = json.loads(report_path.read_text())
            report["source_hashes"] = {
                "benchmark": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
            }
            report["metadata_hashes"] = {
                "benchmark": hashlib.sha256(meta_path.read_bytes()).hexdigest(),
            }
            report_path.write_text(json.dumps(report))
            self.assertEqual(
                snapshot(root)["seven_strategy_research"],
                "CURRENT_EXPLORATORY_NOT_VALIDATED",
            )
            meta_path.write_text('{"operator_source_checked":false}')
            self.assertEqual(
                snapshot(root)["seven_strategy_research"],
                "STALE_OR_UNVERIFIED_RESEARCH_REPORT",
            )


if __name__ == "__main__":
    unittest.main()
