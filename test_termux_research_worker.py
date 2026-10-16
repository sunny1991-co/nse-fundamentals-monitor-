import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from termux_research_worker import maintenance_due


class TermuxResearchWorkerTests(unittest.TestCase):
    def test_missing_status_is_due_only_outside_market_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'runtime').mkdir()
            after_close = datetime.fromisoformat('2026-10-16T17:00:00+05:30')
            during_market = datetime.fromisoformat('2026-10-16T11:00:00+05:30')
            self.assertTrue(maintenance_due(root, after_close))
            self.assertFalse(maintenance_due(root, during_market))

    def test_recent_maintenance_is_not_due(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root/'runtime'
            runtime.mkdir()
            now = datetime.fromisoformat('2026-10-16T18:00:00+05:30')
            (runtime/'maintenance_status.json').write_text(json.dumps({
                'updated_at': (now.astimezone(timezone.utc)-timedelta(hours=2)).isoformat()
            }))
            self.assertFalse(maintenance_due(root, now))

    def test_stale_maintenance_is_due_after_close(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root/'runtime'
            runtime.mkdir()
            now = datetime.fromisoformat('2026-10-16T18:00:00+05:30')
            (runtime/'maintenance_status.json').write_text(json.dumps({
                'updated_at': (now.astimezone(timezone.utc)-timedelta(hours=25)).isoformat()
            }))
            self.assertTrue(maintenance_due(root, now))


if __name__ == '__main__':
    unittest.main()
