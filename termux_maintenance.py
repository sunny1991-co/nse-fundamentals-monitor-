"""Bounded non-Upstox maintenance for the Android/Termux host.

This refreshes official-source fundamentals/sector evidence and readiness
reports. It never connects to a broker, sends an order, or enables alerts.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

CODE_ROOT = Path(__file__).resolve().parent
load_dotenv(CODE_ROOT/'.env', interpolate=False)

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

STEPS = [
    # Research only; no trade/shortlist auto-promotion.
    ('official_mainboard_discovery',
     [sys.executable, str(CODE_ROOT/'nse_mainboard_discovery.py')], 2*60),
    ('mainboard_evidence_gap_inventory',
     [sys.executable, str(CODE_ROOT/'nse_discovery_evidence_gaps.py')], 2*60),
    # Find original NSE archived annual report PDFs for ALL current
    # mainboard companies in a bounded daily batch. Index/PDF review ONLY.
    ('official_annual_pdf_source_index',
     [sys.executable, str(CODE_ROOT/'nse_official_annual_pdf_auto_index.py'),
      '--limit', '25', '--download', '2'], 8*60),
    # NSE Indices' broad constituent industry reference; never 4-tier strict sector.
    ("official_index_industry_reference",
     [sys.executable, str(CODE_ROOT/'nse_index_industry_reference.py')], 2*60),
    ("legacy_annual_verify",
     [sys.executable, str(CODE_ROOT/'nse_legacy_xbrl_verify.py'), '--limit', '100'], 20*60),
    # Audited consolidated NSE XBRL only; bounded new company-years each day.
    # On NSE access blocks or stale discovery, report a failure; NEVER promote a
    # stock on vendor financials, and never overwrite FY baselines.
    ("official_discovered_annual_catchup",
     [sys.executable, str(CODE_ROOT/'nse_annual_history.py'),
      '--include-discovered', '--limit', '25'], 20*60),
    ("sector_refresh",
     [sys.executable, str(CODE_ROOT/'nse_sector_classification.py'), '--limit', '300'], 15*60),
    ("quarterly_refresh",
     [sys.executable, str(CODE_ROOT/'nse_quarterly_monitor.py'), '--limit', '600', '--max-minutes', '20'], 25*60),
    ("annual_quality",
     [sys.executable, str(CODE_ROOT/'annual_fundamental_quality.py')], 10*60),
    ("fast_fundamental",
     [sys.executable, str(CODE_ROOT/'fast_fundamental_screen.py')], 10*60),
    ("final_shortlist",
     [sys.executable, str(CODE_ROOT/'final_fundamental_shortlist.py')], 10*60),
    ("research_swing_assistant_watchlist",
     [sys.executable, str(CODE_ROOT/'swing_ai_assistant_scan.py')], 10*60),
    ("reconcile_approved_paper_eod",
     [sys.executable, str(CODE_ROOT/'swing_ai_assistant_eod.py')], 10*60),
    ("readiness",
     [sys.executable, str(CODE_ROOT/'swing_readiness.py')], 5*60),
    # Frozen retrospective comparisons are manual only. Their historical
    # cutoff is 2026-10-15 and rerunning them daily is not forward testing.
    # Keep their private reports intact; do not promote failed strategies.
]


def csv_first(path: Path) -> dict:
    try:
        with path.open(encoding='utf-8-sig', newline='') as stream:
            return next(csv.DictReader(stream), {})
    except (OSError, StopIteration):
        return {}


def summary(root: Path) -> dict:
    annual = csv_first(root/'annual_fundamental_quality_summary.csv')
    # Prefer the FUNDAMENTAL_SCREEN row rather than the first FY row.
    try:
        with (root/'annual_fundamental_quality_summary.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        annual = next((r for r in rows if r.get('Financial_Year') == 'FUNDAMENTAL_SCREEN'), annual)
    except OSError:
        pass
    final = csv_first(root/'final_fundamental_screen_summary.csv')
    fast = csv_first(root/'fast_fundamental_screen_summary.csv')
    quarterly = csv_first(root/'quarterly_collection_status.csv')
    sectors = {'ok': 0, 'other': 0}
    try:
        with (root/'nse_sector_map.csv').open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                if row.get('Lookup_Status') == 'OK':
                    sectors['ok'] += 1
                else:
                    sectors['other'] += 1
    except OSError:
        pass
    history_state = {}
    try:
        history_state = json.loads((root/'runtime'/'nse_eod_history_status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        history_state = {}
    try:
        discovery = json.loads((root/'runtime'/'nse_mainboard_discovery_status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        discovery = {}
    try:
        discovery_gaps = json.loads((root/'runtime'/'nse_mainboard_evidence_gaps_status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        discovery_gaps = {}
    try:
        annual_pdf = json.loads((root/'runtime'/'nse_official_annual_pdf_collector_status.json').read_text(encoding='utf-8'))
    except (OSError,ValueError,TypeError):
        annual_pdf = {}
    return {
        'official_annual_pdf_index_state': annual_pdf.get('state','NOT_STARTED'),
        'official_annual_pdf_index_symbols_checked': int(annual_pdf.get('indexed_company_total') or 0),
        'official_annual_report_pdf_review_candidates': int(annual_pdf.get('total_candidate_pdf_index_rows') or 0),
        'nse_mainboard_evidence_gaps_state': discovery_gaps.get('state', 'NOT_STARTED'),
        'nse_mainboard_missing_fy_rows': int(discovery_gaps.get('missing_at_least_one_fy_row') or 0),
        'nse_mainboard_discovery_state': discovery.get('state', 'NOT_STARTED'),
        'nse_mainboard_eq_count': int(discovery.get('mainboard_eq_count') or 0),
        'outside_annual_baseline_watch_only': int(discovery.get('outside_annual_baseline') or 0),
        'discovery_research_queue_count': int(discovery.get('research_queue_count') or 0),
        'annual_full_pass': int(annual.get('Year_Data_Quality_Pass') or 0),
        'two_year_baseline_pass': int(annual.get('Six_Core_Fields_Complete') or 0),
        'final_shortlist': int(final.get('Final_Shortlist_Count') or 0),
        'provisional_fast_fundamental_pass': int(fast.get('Fast_Fundamental_Pass_Count') or 0),
        'quarterly_status': quarterly.get('Collection_Status', 'MISSING'),
        'quarterly_verified_rows': int(quarterly.get('Verified_Quarter_Rows') or 0),
        'quarterly_qualified_rows': int(quarterly.get('Qualified_Comparison_Rows') or 0),
        'sector_verified_rows': sectors['ok'],
        'sector_missing_or_error_rows': sectors['other'],
        'adjusted_history': 'PRESENT' if (root/'market_ohlcv.csv').is_file() else 'MISSING',
        'official_eod_history_state': history_state.get('state', 'NOT_STARTED'),
        'official_eod_remaining_weekdays': int(
            (history_state.get('daily') or {}).get('remaining_weekdays') or 0
        ),
        'official_eod_actions': (history_state.get('corporate_actions') or {}).get('status', 'NOT_STARTED'),
        'upstox': 'INTENTIONALLY_DEFERRED',
        'orders': 'MANUAL_ONLY',
    }


def main() -> int:
    root = data_root()
    runtime = root/'runtime'
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = runtime/'maintenance.log'
    status_path = runtime/'maintenance_status.json'

    # Explicitly keep this maintenance path broker-independent.
    env = dict(os.environ)
    env.pop('UPSTOX_ANALYTICS_TOKEN', None)
    env['SMS_ENABLED'] = '0'

    results = []
    local_failure = False
    with log_path.open('a', encoding='utf-8', buffering=1) as log:
        log.write('\n=== maintenance '+datetime.now(timezone.utc).isoformat()+' ===\n')
        for name, command, timeout in STEPS:
            started = datetime.now(timezone.utc).isoformat()
            log.write(f'\n[{name}] start {started}\n')
            log.flush()
            try:
                completed = subprocess.run(
                    command, cwd=CODE_ROOT, env=env, stdout=log,
                    stderr=subprocess.STDOUT, timeout=timeout, check=False,
                )
                code = completed.returncode
                state = 'OK' if code == 0 else 'FAILED'
                local_failure = local_failure or code != 0
            except subprocess.TimeoutExpired:
                code = None
                state = 'TIMEOUT'
                local_failure = True
                log.write(f'[{name}] timeout after {timeout}s\n')
            except OSError as exc:
                code = None
                state = 'START_ERROR'
                local_failure = True
                log.write(f'[{name}] start error: {type(exc).__name__}\n')
            results.append({'step': name, 'state': state, 'returncode': code})
            atomic_status(status_path, {
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'state': 'RUNNING',
                'current_or_last_step': name,
                'steps': results,
                'upstox': 'INTENTIONALLY_DEFERRED',
                'orders': 'MANUAL_ONLY',
            })

    final = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'state': 'LOCAL_STEP_FAILURE' if local_failure else 'COMPLETED',
        'steps': results,
        'evidence': summary(root),
        'upstox': 'INTENTIONALLY_DEFERRED',
        'orders': 'MANUAL_ONLY',
    }
    atomic_status(status_path, final)
    print(json.dumps(final, indent=2))
    return 1 if local_failure else 0


if __name__ == '__main__':
    raise SystemExit(main())
