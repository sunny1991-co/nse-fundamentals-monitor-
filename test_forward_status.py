import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from research_monitor.forward_status import summary

class ForwardStatusTests(unittest.TestCase):
    def test_missing_recent_stale_and_invalid_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            now=datetime(2026,10,16,15,tzinfo=timezone.utc)
            self.assertEqual(summary(root,now)['report_freshness'],'UNKNOWN')
            path=root/'runtime/experiments/three-hypotheses-forward-v1/status.json'
            path.parent.mkdir(parents=True)
            report=dict(checked_at=now.isoformat(),state='FORWARD_RESEARCH_RUNNING',counts={})
            path.write_text(json.dumps(report))
            original=path.read_bytes()
            current=summary(root,now)
            self.assertEqual(current['next_step'],'WAIT_FOR_NEW_ELIGIBLE_SIGNAL')
            self.assertEqual(current['worker_liveness'],'NOT_CHECKED')
            self.assertFalse(current['live_eligible'])
            self.assertEqual(summary(root,now+timedelta(days=3))['next_step'],'CHECK_BACKEND_CLOCK_AND_ANALYSIS_LOG')
            self.assertEqual(path.read_bytes(),original)
            report['counts']={'CLOSED':-1}
            path.write_text(json.dumps(report))
            self.assertEqual(summary(root,now)['report_freshness'],'UNKNOWN')
