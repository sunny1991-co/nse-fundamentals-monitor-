"""Offline regression checks for safe local watchdog recovery."""
import unittest
from unittest.mock import patch

from research_monitor.auto_doctor import dashboard_ok, recoverable


class WatchdogRegressionTests(unittest.TestCase):
    def test_loopback_probe_supplies_expected_dashboard_host(self):
        class Reply:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch("research_monitor.auto_doctor.urlopen", return_value=Reply()) as opener:
            self.assertTrue(dashboard_ok())
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8787/healthz")
        self.assertEqual(request.get_header("Host"), "localhost")

    def test_simultaneous_incidents_spend_only_one_restart_allowance(self):
        epoch = 1000000.0
        issues = [
            {"code": "STALE", "component": name, "auto_repair": True}
            for name in ("worker", "history")
        ]
        first, journal = recoverable(issues, {}, epoch=epoch)
        self.assertEqual(first, [])
        second, journal = recoverable(issues, journal, epoch=epoch + 120)
        self.assertEqual(second, ["worker"])
        self.assertEqual(journal["restart_history"], {"worker": [epoch + 120]})
        third, journal = recoverable(issues, journal, epoch=epoch + 240)
        self.assertEqual(third, ["history"])
        self.assertEqual(journal["restart_history"]["worker"], [epoch + 120])
        self.assertEqual(journal["restart_history"]["history"], [epoch + 240])


if __name__ == "__main__":
    unittest.main()
