"""Low-impact Termux background catch-up for official NSE EOD history.

This process is supervised separately from the latency-sensitive swing worker.
It never reads broker credentials. Every cycle advances a bounded number of
missing NSE bhavcopy days, then sleeps for several hours.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import datetime, date, timedelta, timezone

from dotenv import load_dotenv

CODE_ROOT = Path(__file__).resolve().parent
load_dotenv(CODE_ROOT/'.env', interpolate=False)

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status, IST
from research_monitor.nse_history_calendar import expected_archive_day


def publish_worker_status(path: Path, value: dict, owner_only: bool = False) -> bool:
    """Publish status without allowing an older worker to overwrite a newer one."""
    pid = os.getpid()
    if owner_only:
        try:
            current = json.loads(path.read_text(encoding='utf-8'))
            current_pid = current.get('pid')
            if current_pid not in (None, pid):
                return False
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    payload = dict(value)
    payload['pid'] = pid
    atomic_status(path, payload)
    return True


def run_post_publish_validation(root: Path, env: dict, log) -> dict:
    """Run the fixed-rule backtest once per newly published history SHA."""
    metadata_path = root/'market_data_metadata.json'
    marker_path = root/'runtime'/'history_backtest_done.json'
    try:
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        digest = str(metadata.get('sha256') or '').strip()
    except (OSError, ValueError, TypeError):
        return {'state': 'WAITING_FOR_PUBLISHED_HISTORY_METADATA'}
    if not digest:
        return {'state': 'WAITING_FOR_PUBLISHED_HISTORY_METADATA'}

    try:
        marker = json.loads(marker_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        marker = {}
    if (
        marker.get('sha256') == digest
        and marker.get('state') == 'BACKTEST_SCREEN_PAPER_READINESS_COMPLETE'
    ):
        return {'state': 'ALREADY_VALIDATED_FOR_CURRENT_HISTORY', 'sha256': digest}

    results = {}
    for name, script, script_args, timeout in (
        ('annual_quality_returncode', 'annual_fundamental_quality.py', [], 10*60),
        ('final_shortlist_returncode', 'final_fundamental_shortlist.py', [], 10*60),
        ('fast_fundamental_returncode', 'fast_fundamental_screen.py', [], 10*60),
        ('backtest_returncode', 'swing_backtest.py', [], 45*60),
        ('technical_screen_returncode', 'swing_technical_screener.py', ['--reuse-current-backtest'], 20*60),
        ('fast_research_returncode', 'swing_fast_research.py', [], 10*60),
        ('paper_returncode', 'swing_paper_monitor.py', [], 10*60),
        ('readiness_returncode', 'swing_readiness.py', [], 5*60),
    ):
        log.write(f'\n[post-publish] {script}\n')
        log.flush()
        try:
            completed = subprocess.run(
                [sys.executable, str(CODE_ROOT/script), *script_args],
                cwd=CODE_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                timeout=timeout, check=False,
            )
            results[name] = completed.returncode
        except subprocess.TimeoutExpired:
            results[name] = None
            log.write(f'[post-publish] {script} timeout\n')
        except OSError as exc:
            results[name] = None
            log.write(f'[post-publish] {script} start error: {type(exc).__name__}\n')
        if results[name] != 0:
            break

    marker = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'sha256': digest,
        **results,
        'state': (
            'BACKTEST_SCREEN_PAPER_READINESS_COMPLETE'
            if all(results.get(key) == 0 for key in (
                'annual_quality_returncode',
                'final_shortlist_returncode',
                'fast_fundamental_returncode',
                'backtest_returncode',
                'technical_screen_returncode',
                'fast_research_returncode',
                'paper_returncode',
                'readiness_returncode',
            ))
            else 'POST_PUBLISH_VALIDATION_INCOMPLETE'
        ),
        'orders': 'MANUAL_ONLY',
    }
    atomic_status(marker_path, marker)
    return marker

# Fast catch-up still preserves source/hash/date/corporate-action validation.
# It changes throughput only; evidence gates are not relaxed. HTTP 403/429
# remains an immediate stop condition inside the collector.
INTERVAL_SECONDS = 2 * 60 * 60
CATCHUP_INTERVAL_SECONDS = 3 * 60
BATCH_DAYS = 250
MAX_MINUTES = 30


def legacy_history_recheck_needed(collector: dict, now: datetime) -> bool:
    """Skip costly historic republish when the last completed NSE session is covered.

    An absent, incomplete, corrupted or unrecognized status always triggers
    normal collector verification. This NEVER assumes raw new-company data is
    eligible for the strict adjusted historical feed.
    """
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    if not (
        collector.get('state') == 'ADJUSTED_HISTORY_PUBLISHED'
        and (collector.get('publish') or {}).get('published') is True
        and collector.get('daily_range_complete') is True
        and collector.get('corporate_action_range_complete') is True
        and not collector.get('suspicious_consecutive_no_file_gap', True)
    ):
        return True
    end = now.astimezone(IST).date() - timedelta(days=1)
    last_expected = end
    for _ in range(14):
        if expected_archive_day(last_expected):
            break
        last_expected -= timedelta(days=1)
    else:
        return True
    try:
        recorded_end = date.fromisoformat(str(collector.get('range_end', '')))
    except ValueError:
        return True
    # A future-dated source range is invalid, not a reason to skip auditing.
    if recorded_end > end:
        return True
    return recorded_end < last_expected


def next_cycle_delay(collector: dict, code: int | None,
                     raw_backfill: dict | None = None) -> tuple[int, int, bool]:
    daily = collector.get('daily') or {}
    remaining = int(
        daily.get('remaining_archive_days', daily.get('remaining_weekdays', 0)) or 0
    )
    blocked = bool(daily.get('blocked')) or collector.get('state') == 'BLOCKED_BY_NSE_ACCESS'
    fast_catchup = (
        code == 0
        and remaining > 0
        and not blocked
        and collector.get('state') != 'ADJUSTED_HISTORY_PUBLISHED'
    )
    # The old adjusted archive may be complete while thousands of original
    # EQ research days are still missing. Run short raw catch-up cycles without
    # wasting 30 minutes republishing identical adjusted data each time.
    raw = raw_backfill or {}
    raw_pending = int(raw.get('estimated_outstanding_expected_sessions') or 0)
    raw_blocked = raw.get('source_blocked') is True or raw.get('state') in (
        'NSE_ACCESS_BLOCKED', 'MAINBOARD_RESEARCH_BACKFILL_BLOCKED',
        'ARCHIVE_MISSING_BATCH_REVIEW', 'UNEXPECTED_SOURCE_RESULT',
    )
    research_fast = raw.get('state') == 'RESEARCH_UNADJUSTED_EOD_PROGRESS' and (
        raw_pending > 0 and not raw_blocked
    )
    fast_catchup = fast_catchup or research_fast
    wait_seconds = CATCHUP_INTERVAL_SECONDS if fast_catchup else INTERVAL_SECONDS
    return wait_seconds, remaining, fast_catchup


def main() -> int:
    root = data_root()
    runtime = root/'runtime'
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    status_path = runtime/'history_worker_status.json'
    log_path = runtime/'history_worker.log'
    lock_path = runtime/'history_worker.lock'

    lock = lock_path.open('a+')
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('Official history worker already running.', flush=True)
        lock.close()
        return 2

    stopping = False
    active_child = None

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        child = active_child
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    env = dict(os.environ)
    env.pop('UPSTOX_ANALYTICS_TOKEN', None)
    env['SMS_ENABLED'] = '0'

    publish_worker_status(status_path, {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'state': 'STARTING',
        'upstox': 'NOT_USED',
        'orders': 'MANUAL_ONLY',
    })

    try:
        # Let the main swing worker/dashboard finish startup first.
        initial_deadline = time.monotonic() + 20
        while not stopping and time.monotonic() < initial_deadline:
            time.sleep(1)

        while not stopping:
            started = datetime.now(timezone.utc).isoformat()
            publish_worker_status(status_path, {
                'updated_at': started,
                'state': 'COLLECTING',
                'upstox': 'NOT_USED',
                'orders': 'MANUAL_ONLY',
            })
            # New-company raw history runs FIRST so the slow but already
            # completed legacy adjusted collector never starves it.
            # Run a separate, bounded all-EQ research backfill. Its database
            # is UNADJUSTED and cannot enter the trade/backtest feed.
            discovery_cycle = {'state': 'NOT_RUN'}
            if stopping:
                break
            publish_worker_status(status_path, {
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'state': 'MAINBOARD_RESEARCH_COLLECTING',
                'phase': 'RAW_HISTORY_FIRST',
                'upstox': 'NOT_USED', 'orders': 'MANUAL_ONLY',
            })
            try:
                with log_path.open('a', encoding='utf-8', buffering=1) as log:
                    log.write('\n=== automatic all-mainboard research cycle ===\n')
                    log.flush()
                    backfill = subprocess.run(
                        [sys.executable, str(CODE_ROOT/'nse_mainboard_history_backfill.py'),
                         '--days', '60', '--max-minutes', '12'],
                        cwd=CODE_ROOT, env=env, stdout=log,
                        stderr=subprocess.STDOUT, timeout=13*60, check=False,
                    )
                    log.write('=== raw EOD finished; technical radar starts ===\\n')
                    log.flush()
                    radar = subprocess.run(
                        [sys.executable, str(CODE_ROOT/'nse_mainboard_technical_radar.py')],
                        cwd=CODE_ROOT, env=env, stdout=log,
                        stderr=subprocess.STDOUT, timeout=5*60, check=False,
                    )
                backfill_state = json.loads(
                    (runtime/'mainboard_raw_research_history_status.json').read_text(
                        encoding='utf-8')
                )
                radar_state = json.loads(
                    (runtime/'nse_mainboard_auto_research_radar_status.json').read_text(
                        encoding='utf-8')
                )
                discovery_cycle = {
                    'state': 'RESEARCH_CYCLE_COMPLETED' if backfill.returncode == 0 and
                             radar.returncode == 0 else 'RESEARCH_CYCLE_PARTIAL',
                    'backfill': backfill_state.get('state', 'UNKNOWN'),
                    'archive_days_added': int(backfill_state.get(
                        'archive_days_added_this_run') or 0),
                    'archive_days_pending': int(backfill_state.get(
                        'estimated_outstanding_expected_sessions') or 0),
                    'radar': radar_state.get('state', 'UNKNOWN'),
                    'unadjusted_research_candidates': int(radar_state.get(
                        'unadjusted_breakout_research_candidates') or 0),
                    'trade_eligible': False,
                }
            except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
                discovery_cycle = {
                    'state':'RESEARCH_CYCLE_ERROR',
                    'error_type':type(exc).__name__,
                    'trade_eligible':False,
                }


            if stopping:
                break
            code = None
            collector = {}
            try:
                collector = json.loads(
                    (runtime/'nse_eod_history_status.json').read_text(encoding='utf-8')
                )
            except (OSError, ValueError, TypeError):
                collector = {}
            legacy_needed = legacy_history_recheck_needed(
                collector, datetime.now(timezone.utc))
            publish_worker_status(status_path, {
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'state': 'LEGACY_ADJUSTED_COLLECTING' if legacy_needed else 'LEGACY_ADJUSTED_UP_TO_DATE',
                'phase': 'VERIFIED_ADJUSTED_HISTORY',
                'raw_research_cycle': discovery_cycle,
                'legacy_collector_skipped': not legacy_needed,
                'upstox': 'NOT_USED', 'orders': 'MANUAL_ONLY',
            })
            if not legacy_needed:
                code = 0
                with log_path.open('a', encoding='utf-8', buffering=1) as log:
                    log.write('=== adjusted NSE archive already covers all reviewed completed sessions; no republish ===\\n')
            else:
                try:
                    with log_path.open('a', encoding='utf-8', buffering=1) as log:
                        log.write(f'\n=== adjusted history cycle {started} ===\n')
                        log.flush()
                        active_child = subprocess.Popen(
                            [
                                sys.executable, str(CODE_ROOT/'swing_nse_history.py'),
                                '--days', str(BATCH_DAYS),
                                '--max-minutes', str(MAX_MINUTES),
                            ],
                            cwd=CODE_ROOT,
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                        deadline = time.monotonic() + (MAX_MINUTES + 2) * 60
                        while active_child.poll() is None and not stopping and time.monotonic() < deadline:
                            time.sleep(1)
                        if active_child.poll() is None:
                            try:
                                os.killpg(active_child.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                            try:
                                active_child.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                try:
                                    os.killpg(active_child.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                                active_child.wait()
                        code = active_child.returncode
                        if not stopping and time.monotonic() >= deadline:
                            code = None
                            log.write('collector timeout; will retry next cycle\n')
                        active_child = None
                except OSError as exc:
                    code = None
                    with log_path.open('a', encoding='utf-8') as log:
                        log.write(f'collector start error: {type(exc).__name__}\n')
            post_publish = {'state': 'NOT_READY'}
            if collector.get('state') == 'ADJUSTED_HISTORY_PUBLISHED':
                try:
                    with log_path.open('a', encoding='utf-8', buffering=1) as log:
                        post_publish = run_post_publish_validation(root, env, log)
                except OSError:
                    post_publish = {'state': 'POST_PUBLISH_LOG_ERROR'}

            daily = collector.get('daily') or {}
            raw_status = {}
            try:
                raw_status = json.loads(
                    (runtime/'mainboard_raw_research_history_status.json').read_text(
                        encoding='utf-8')
                )
            except (OSError, ValueError, TypeError):
                raw_status = {}
            wait_seconds, remaining, fast_catchup = next_cycle_delay(
                collector, code, raw_backfill=raw_status)
            next_run = time.time() + wait_seconds
            publish_worker_status(status_path, {
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'state': 'SLEEPING',
                'collector_returncode': code,
                'collector_state': collector.get('state', 'STATUS_UNAVAILABLE'),
                'remaining_weekdays': remaining,
                'remaining_archive_days': remaining,
                'unverified_no_file_days': int(daily.get('unverified_no_file_days') or 0),
                'fast_catchup': fast_catchup,
                'next_run_seconds': wait_seconds,
                'post_publish_validation': post_publish,
                'mainboard_auto_research': discovery_cycle,
                'legacy_collector_skipped': not legacy_needed,
                'next_run_epoch': next_run,
                'upstox': 'NOT_USED',
                'orders': 'MANUAL_ONLY',
            })

            while not stopping and time.time() < next_run:
                time.sleep(min(10, max(1, next_run-time.time())))
        return 0
    finally:
        if active_child is not None and active_child.poll() is None:
            try:
                os.killpg(active_child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        publish_worker_status(status_path, {
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'state': 'STOPPED',
            'upstox': 'NOT_USED',
            'orders': 'MANUAL_ONLY',
        }, owner_only=True)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
