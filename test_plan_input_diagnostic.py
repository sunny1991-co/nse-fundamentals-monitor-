import json
import tempfile
import unittest
from pathlib import Path
from research_monitor.readiness import EVIDENCE_FILES, candidate_manifest
from diagnose_plan_inputs import diagnose

class PlanInputDiagnosticTests(unittest.TestCase):
    def test_names_changed_input_without_rewriting_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in EVIDENCE_FILES:
                (root/name).write_text('header\n')
            path=root/'swing_candidates_manifest.json'
            path.write_text(json.dumps(candidate_manifest(root)))
            self.assertEqual(diagnose(root)['next_step'],'NO_REFRESH_NEEDED_WAIT_FOR_SCHEDULED_ANALYSIS')
            original=path.read_bytes()
            (root/'final_fundamental_shortlist.csv').write_text('changed\n')
            report=diagnose(root)
            self.assertEqual(report['state'],'PLAN_INPUTS_CHANGED')
            self.assertEqual(report['changed_files'],['final_fundamental_shortlist.csv'])
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(report['writes_performed'],0)

    def test_invalid_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'swing_candidates_manifest.json').write_text('[]')
            self.assertEqual(diagnose(root)['state'],'MANIFEST_MISSING_OR_INVALID')
