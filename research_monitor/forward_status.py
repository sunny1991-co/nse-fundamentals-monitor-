"""Read-only status summary; a recent report is not worker liveness."""
import json
from datetime import datetime, timezone
from pathlib import Path


def summary(root, now=None):
    now = now or datetime.now(timezone.utc)
    result = dict(state='NOT_STARTED_OR_UNREADABLE', last_checked_at=None,
                  report_freshness='UNKNOWN', counts={},
                  live_eligible=False, worker_liveness='NOT_CHECKED')
    path = Path(root)/'runtime'/'experiments'/'three-hypotheses-forward-v1'/'status.json'
    try:
        report = json.loads(path.read_text())
        stamp = datetime.fromisoformat(report['checked_at'])
        age = (now-stamp).total_seconds()
        counts = report.get('counts', {})
        if not isinstance(counts, dict) or any(
                not isinstance(k, str) or type(v) is not int or v < 0
                for k, v in counts.items()):
            raise ValueError('Invalid counts')
        if not isinstance(report['state'], str):
            raise ValueError('Invalid state')
        result.update(state=report['state'], last_checked_at=report['checked_at'],
                      report_freshness=('RECENT_REPORT' if 0 <= age <= 48*3600 else
                                        'STALE_OR_FUTURE_REPORT'),
                      counts=counts, enrolled_at=report.get('enrolled_at'),
                      reason=report.get('reason'),
                      next_step=('WAIT_FOR_NEW_ELIGIBLE_SIGNAL' if not counts and
                                 report['state']=='FORWARD_RESEARCH_RUNNING' else
                                 'REVIEW_RECORDED_STATE'))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    if result['report_freshness']=='STALE_OR_FUTURE_REPORT':
        result['next_step']='CHECK_BACKEND_CLOCK_AND_ANALYSIS_LOG'
    return result
