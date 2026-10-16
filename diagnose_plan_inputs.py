"""Read-only explanation of candidate manifest mismatches. Never rewrites evidence."""
import json
from pathlib import Path
from research_monitor.paths import data_root
from research_monitor.readiness import EVIDENCE_FILES, file_hash, candidate_rows


def diagnose(root):
    root = Path(root)
    try:
        manifest = json.loads((root/'swing_candidates_manifest.json').read_text())
        saved = manifest.get('files', {})
        if not isinstance(saved, dict):
            raise ValueError('Invalid manifest')
    except (OSError, ValueError, TypeError, AttributeError):
        return dict(state='MANIFEST_MISSING_OR_INVALID', changed_files=[], writes_performed=0)
    changed, missing = [], []
    for name in EVIDENCE_FILES:
        try:
            if saved.get(name) != file_hash(root/name):
                changed.append(name)
        except OSError:
            missing.append(name)
    return dict(state=candidate_rows(root)[1], changed_files=changed,
        missing_files=missing, generated_at=manifest.get('generated_at'),
        writes_performed=0,
        next_step='Regenerate candidates from matching verified inputs; do not edit the manifest to bypass validation.')


if __name__ == '__main__':
    print(json.dumps(diagnose(data_root()), indent=2))
