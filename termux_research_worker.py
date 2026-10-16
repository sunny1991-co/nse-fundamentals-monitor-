"""Daily broker-independent fundamentals/sector research maintenance on Termux.

Runs only outside the normal market session and never reads Upstox credentials.
It refreshes legacy annual verification, official sector lookups, quarterly
filings, annual quality, final shortlist, and readiness. Official EOD history
is handled by termux_history_worker.py to avoid duplicate archive downloads.
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
from datetime import datetime, time as dt_time, timezone

from dotenv import load_dotenv

CODE_ROOT = Path(__file__).resolve().parent
load_dotenv(CODE_ROOT/'.env', interpolate=False)

from research_monitor.paths import data_root
from research_monitor.schedule import IST, atomic_status

CHECK_INTERVAL_SECONDS = 30 * 60
MIN_AGE_SECONDS = 20 * 60 * 60


def maintenance_due(root: Path, now: datetime) -> bool:
    if now.tzinfo is None:
        raise ValueError('Timezone-aware clock required')
    local = now.astimezone(IST)
    # Never start this heavy refresh during the normal cash-market session.
    if dt_time(8, 30) <= local.time() < dt_time(16, 15):
        return False
    path = root/'runtime'/'maintenance_status.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        stamp = datetime.fromisoformat(payload['updated_at'])
        if stamp.tzinfo is None:
            return True
        age = (now.astimezone(timezone.utc)-stamp.astimezone(timezone.utc)).total_seconds()
        return age >= MIN_AGE_SECONDS
    except (OSError, ValueError, KeyError, TypeError):
        return True


def main() -> int:
    root = data_root()
    runtime = root/'runtime'
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    status_path = runtime/'research_worker_status.json'
    log_path = runtime/'research_worker.log'
    lock = (runtime/'research_worker.lock').open('a+')
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('Research maintenance worker already running.', flush=True)
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

    try:
        while not stopping:
            now = datetime.now(timezone.utc)
            if maintenance_due(root, now):
                atomic_status(status_path, {
                    'updated_at': now.isoformat(),
                    'state': 'RUNNING_MAINTENANCE',
                    'upstox': 'NOT_USED',
                    'orders': 'MANUAL_ONLY',
                })
                code = None
                try:
                    with log_path.open('a', encoding='utf-8', buffering=1) as log:
                        log.write(f'\n=== research maintenance {now.isoformat()} ===\n')
                        active_child = subprocess.Popen(
                            [sys.executable, str(CODE_ROOT/'termux_maintenance.py')],
                            cwd=CODE_ROOT,
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                        deadline = time.monotonic() + 90*60
                        while active_child.poll() is None and not stopping and time.monotonic() < deadline:
                            time.sleep(2)
                        if active_child.poll() is None:
                            try:
                                os.killpg(active_child.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                            try:
                                active_child.wait(timeout=15)
                            except subprocess.TimeoutExpired:
                                try:
                                    os.killpg(active_child.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                                active_child.wait()
                        code = active_child.returncode
                        active_child = None
                except OSError as exc:
                    with log_path.open('a', encoding='utf-8') as log:
                        log.write(f'maintenance start error: {type(exc).__name__}\n')
                atomic_status(status_path, {
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'state': 'WAITING_NEXT_DAILY_WINDOW',
                    'last_returncode': code,
                    'upstox': 'NOT_USED',
                    'orders': 'MANUAL_ONLY',
                })
            else:
                atomic_status(status_path, {
                    'updated_at': now.isoformat(),
                    'state': 'WAITING_NEXT_DAILY_WINDOW',
                    'upstox': 'NOT_USED',
                    'orders': 'MANUAL_ONLY',
                })

            deadline = time.monotonic() + CHECK_INTERVAL_SECONDS
            while not stopping and time.monotonic() < deadline:
                time.sleep(min(10, max(1, deadline-time.monotonic())))
        return 0
    finally:
        if active_child is not None and active_child.poll() is None:
            try:
                os.killpg(active_child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        atomic_status(status_path, {
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'state': 'STOPPED',
            'upstox': 'NOT_USED',
            'orders': 'MANUAL_ONLY',
        })
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
