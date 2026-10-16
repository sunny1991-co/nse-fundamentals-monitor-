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
from datetime import datetime, timezone

from dotenv import load_dotenv

CODE_ROOT = Path(__file__).resolve().parent
load_dotenv(CODE_ROOT/'.env', interpolate=False)

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

# Initial catch-up is intentionally bounded but faster than the first 40-day
# proof run. ~100 archive days every two hours keeps request volume moderate
# while finishing multi-year history in roughly 1-2 days when NSE stays
# available. Once complete, the collector becomes a cheap no-op between cycles.
INTERVAL_SECONDS = 2 * 60 * 60
BATCH_DAYS = 100
MAX_MINUTES = 15


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

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    env = dict(os.environ)
    env.pop('UPSTOX_ANALYTICS_TOKEN', None)
    env['SMS_ENABLED'] = '0'

    try:
        # Let the main swing worker/dashboard finish startup first.
        initial_deadline = time.monotonic() + 20
        while not stopping and time.monotonic() < initial_deadline:
            time.sleep(1)

        while not stopping:
            started = datetime.now(timezone.utc).isoformat()
            atomic_status(status_path, {
                'updated_at': started,
                'state': 'COLLECTING',
                'upstox': 'NOT_USED',
                'orders': 'MANUAL_ONLY',
            })
            code = None
            try:
                with log_path.open('a', encoding='utf-8', buffering=1) as log:
                    log.write(f'\n=== history cycle {started} ===\n')
                    result = subprocess.run(
                        [
                            sys.executable, str(CODE_ROOT/'swing_nse_history.py'),
                            '--days', str(BATCH_DAYS),
                            '--max-minutes', str(MAX_MINUTES),
                        ],
                        cwd=CODE_ROOT,
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=(MAX_MINUTES + 2) * 60,
                        check=False,
                    )
                    code = result.returncode
            except subprocess.TimeoutExpired:
                code = None
                with log_path.open('a', encoding='utf-8') as log:
                    log.write('collector timeout; will retry next cycle\n')
            except OSError as exc:
                code = None
                with log_path.open('a', encoding='utf-8') as log:
                    log.write(f'collector start error: {type(exc).__name__}\n')

            collector = {}
            try:
                collector = json.loads(
                    (runtime/'nse_eod_history_status.json').read_text(encoding='utf-8')
                )
            except (OSError, ValueError, TypeError):
                collector = {}

            next_run = time.time() + INTERVAL_SECONDS
            atomic_status(status_path, {
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'state': 'SLEEPING',
                'collector_returncode': code,
                'collector_state': collector.get('state', 'STATUS_UNAVAILABLE'),
                'remaining_weekdays': int(
                    (collector.get('daily') or {}).get('remaining_weekdays') or 0
                ),
                'next_run_epoch': next_run,
                'upstox': 'NOT_USED',
                'orders': 'MANUAL_ONLY',
            })

            while not stopping and time.time() < next_run:
                time.sleep(min(10, max(1, next_run-time.time())))
        return 0
    finally:
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
