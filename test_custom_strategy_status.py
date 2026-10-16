"""Read-only Termux project status must reject stale custom research."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from project_status import CODE_ROOT, snapshot

CODE_PATHS = (
    "swing_custom_strategy_research.py", "swing_backtest.py",
    "swing_strategy.py", "swing_pullback_research.py",
    "research_monitor/exploratory_ranking.py",
)


class CustomStatusTests(unittest.TestCase):
    def make(self, root):
        digest = hashlib.sha256(b"".join(
            (CODE_ROOT / p).read_bytes() for p in CODE_PATHS
        )).hexdigest()
        file = (root / "runtime" / "experiments"
                / "custom-dip-reclaim-research-v1" / "comparison.json")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({
            "scope": "CUSTOM_STRATEGY_EXPLORATORY_NOT_VALIDATED",
            "implementation_sha256": digest,
            "data_sha256": "synthetic-not-real",
            "validation_status": "NOT_VALIDATED",
            "live_eligible": False,
            "verified_profitable_strategies": 0,
            "orders": "DISABLED",
            "research_selection": {
                "research_leader": "custom-dip-reclaim-v1",
            },
            "summary": {
                "custom-dip-reclaim-v1": {
                    "pooled_completed_trades": 40,
                    "pooled_completed_mean_net_return": 0.10,
                }
            },
        }))
        return file

    def test_status_not_loaded_without_matching_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(snapshot(root)["custom_strategy_research"], "NOT_RUN")
            self.make(root)
            self.assertEqual(
                snapshot(root)["custom_strategy_research"],
                "STALE_OR_UNVERIFIED_RESEARCH_REPORT",
            )
            self.assertIsNone(snapshot(root)["custom_strategy_research_leader"])
            (root / "market_data_metadata.json").write_text(
                json.dumps({"sha256": "synthetic-not-real"})
            )
            current = snapshot(root)
            self.assertEqual(
                current["custom_strategy_research"],
                "CURRENT_EXPLORATORY_NOT_VALIDATED",
            )
            self.assertEqual(current["custom_strategy_completed_trades"], 40)
            self.assertFalse(current["custom_strategy_live_eligible"])
            self.assertEqual(current["verified_profitable_strategies"], 0)
            (root / "market_data_metadata.json").write_text(
                json.dumps({"sha256": "changed-input"})
            )
            self.assertIsNone(snapshot(root)["custom_strategy_completed_trades"])

    def test_a_false_live_claim_invalidates_research_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.make(root)
            (root / "market_data_metadata.json").write_text(
                json.dumps({"sha256": "synthetic-not-real"})
            )
            payload = json.loads(path.read_text())
            payload["live_eligible"] = True
            path.write_text(json.dumps(payload))
            self.assertEqual(
                snapshot(root)["custom_strategy_research"],
                "STALE_OR_UNVERIFIED_RESEARCH_REPORT",
            )
            self.assertIsNone(snapshot(root)["custom_strategy_research_leader"])


if __name__ == "__main__":
    unittest.main()
