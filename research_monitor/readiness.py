"""Local evidence status; never opens a provider connection or reports token=live."""
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

EVIDENCE_FILES = ('market_ohlcv.csv', 'market_data_metadata.json',
    'final_fundamental_shortlist.csv', 'final_fundamental_screen_summary.csv',
    'swing_backtest_report.csv', 'swing_trade_candidates.csv')


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def candidate_manifest(root, now=None):
    return dict(generated_at=(now or datetime.now(timezone.utc)).isoformat(),
        scope='CONDITIONAL_RESEARCH_ONLY',
        files={name: file_hash(Path(root)/name) for name in EVIDENCE_FILES})


def candidate_rows(root, now=None):
    """Do not display old plans after inputs change or generation fails."""
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    try:
        meta = json.loads((root/'swing_candidates_manifest.json').read_text())
        age = (now-datetime.fromisoformat(meta['generated_at'])).total_seconds()
        if not 0 <= age <= 86400 or meta['scope'] != 'CONDITIONAL_RESEARCH_ONLY':
            return [], 'PLANS_STALE_OR_INVALID'
        if any(meta['files'].get(name) != file_hash(root/name) for name in EVIDENCE_FILES):
            return [], 'PLAN_INPUTS_CHANGED'
        with (root/'swing_trade_candidates.csv').open(encoding='utf-8') as stream:
            return list(csv.DictReader(stream))[:100], 'CONDITIONAL_RESEARCH_ONLY'
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return [], 'PLANS_NOT_VERIFIED'


def snapshot(root, now=None):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    result = dict(updated_at=now.isoformat(), live_feed='NOT_VERIFIED',
        historical_data='MISSING', fundamental_shortlist='MISSING_OR_INVALID',
        backtest='NOT_VERIFIED', forward_paper='NOT_IMPLEMENTED',
        sms='END_TO_END_NOT_VERIFIED', cloud='DEPLOYMENT_NOT_VERIFIED',
        entry_alerts='BLOCKED')
    try:
        from research_monitor.history import read_history, coverage
        frame, digest = read_history(root)
        result['historical_data'] = 'STRUCTURE_AND_ATTESTATION_VALID'
        result['history_symbols'] = len(coverage(frame))
        result['history_symbols_with_1260_bars'] = sum(x['Minimum_History_Met'] for x in coverage(frame))
        with (root/'swing_backtest_report.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        result['backtest'] = 'REPORT_PRESENT_NOT_FULL_STRATEGY_VALIDATION'
        result['technical_pass_matching_data'] = sum(r.get('Backtest_Pass','').lower() == 'true'
            and r.get('Data_SHA256') == digest for r in rows)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        if (root/'market_ohlcv.csv').exists() and result['historical_data'] == 'MISSING':
            result['historical_data'] = 'INVALID_OR_UNATTESTED'
    try:
        with (root/'final_fundamental_screen_summary.csv').open() as stream:
            row = next(csv.DictReader(stream))
        result['fundamental_shortlist'] = int(row['Final_Shortlist_Count'])
        result['fundamental_report_date'] = row['Report_Generated_On']
    except (OSError, ValueError, KeyError, StopIteration):
        pass
    try:
        worker = json.loads((root/'runtime'/'worker_status.json').read_text())
        age = (now-datetime.fromisoformat(worker['updated_at'])).total_seconds()
        result['live_feed'] = worker.get('live_feed','UNKNOWN') if 0 <= age <= 60 else 'WORKER_STATUS_STALE'
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    _, result['conditional_plans'] = candidate_rows(root, now)
    return result
