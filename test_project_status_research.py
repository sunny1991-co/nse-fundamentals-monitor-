"""Termux status evidence freshness; tests use synthetic, never NSE market data."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from project_status import CODE_ROOT, snapshot


class ProjectResearchStatusTests(unittest.TestCase):
    def create_report(self, root, digest="known-test-digest", code_override=None):
        files = ("swing_pullback_research.py", "swing_backtest.py",
                 "swing_strategy.py")
        code = hashlib.sha256(
            b"".join((CODE_ROOT / n).read_bytes() for n in files)
        ).hexdigest()
        report = {
            "data_sha256": digest,
            "implementation_sha256": code_override or code,
            "validation_status": "NOT_VALIDATED",
            "live_eligible": False,
            "summary": {"breakout-v1": {}, "trend-pullback-research-v1": {}},
            "research_selection": {
                "status": "EXPLORATORY_LEADER_REQUIRES_FRESH_VALIDATION",
                "research_leader": "trend-pullback-research-v1",
            },
        }
        path = (root / "runtime" / "experiments"
                / "trend-pullback-research-v1" / "comparison.json")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def test_no_report_shows_no_claim(self):
        with tempfile.TemporaryDirectory() as d:
            result = snapshot(Path(d))
            self.assertEqual(result["strategy_research"], "NOT_RUN")
            self.assertIsNone(result["exploratory_strategy_leader"])
            self.assertEqual(result["verified_profitable_strategies"], 0)
            self.assertFalse(result["strategy_trading_enabled"])

    def test_fresh_report_must_match_source_and_current_implementation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.create_report(root)
            stale = snapshot(root)
            self.assertEqual(stale["strategy_research"],
                             "STALE_OR_UNVERIFIED_RESEARCH_REPORT")
            self.assertIsNone(stale["exploratory_strategy_leader"])
            (root / "market_data_metadata.json").write_text(
                json.dumps({"sha256": "known-test-digest"}), encoding="utf-8"
            )
            valid = snapshot(root)
            self.assertEqual(valid["strategy_research"],
                             "CURRENT_EXPLORATORY_NOT_VALIDATED")
            self.assertEqual(valid["research_strategies_compared"], 2)
            self.assertEqual(valid["exploratory_strategy_leader"],
                             "trend-pullback-research-v1")
            self.assertEqual(valid["verified_profitable_strategies"], 0)
            self.assertFalse(valid["strategy_trading_enabled"])
            (root / "market_data_metadata.json").write_text(
                json.dumps({"sha256": "updated-data"}), encoding="utf-8"
            )
            self.assertIsNone(snapshot(root)["exploratory_strategy_leader"])

    def test_changed_strategy_code_disallows_stale_selection(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.create_report(root, code_override="old-code")
            (root / "market_data_metadata.json").write_text(
                json.dumps({"sha256": "known-test-digest"}), encoding="utf-8"
            )
            result = snapshot(root)
            self.assertEqual(result["strategy_research"],
                             "STALE_OR_UNVERIFIED_RESEARCH_REPORT")
            self.assertIsNone(result["exploratory_strategy_leader"])


if __name__ == "__main__":
    unittest.main()
