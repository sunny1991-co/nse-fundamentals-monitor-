"""Frozen losing retrospective backtests must not consume daily Termux cycles."""
import unittest

import termux_maintenance


class FrozenResearchMaintenanceTests(unittest.TestCase):
    def test_frozen_research_only_runs_on_explicit_manual_command(self):
        names = [name for name, _, _ in termux_maintenance.STEPS]
        self.assertNotIn("trend_pullback_experiment", names)
        self.assertNotIn("seven_strategy_experiment", names)
        self.assertNotIn("custom_dip_reclaim_experiment", names)
        self.assertNotIn("groww_delivery_experiment", names)
        self.assertNotIn("loss_diagnostic", names)

    def test_official_fundamentals_are_still_maintained(self):
        names = [name for name, _, _ in termux_maintenance.STEPS]
        for task in ("quarterly_refresh", "sector_refresh",
                     "annual_quality", "final_shortlist", "readiness"):
            self.assertIn(task, names)


if __name__ == "__main__":
    unittest.main()
