"""Single-instance Render launcher: shared disk, supervised web and worker.

No credentials are generated here. Enrollment is a private operator action.
"""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

from research_monitor.paths import CODE_ROOT, data_root
from research_monitor.schedule import atomic_status

# GitHub Actions owns these public source snapshots. Never overwrite licensed
# OHLCV, private configuration, auth databases, or the forward paper ledger.
SOURCE_FILES = (
    'FY2425.csv', 'NSE_FY2025-26_Consolidated_Fundamentals.csv',
    'annual_historical_results.csv', 'nse_sector_map.csv',
    'quarterly_results.csv', 'quarterly_change_report.csv',
    'quarterly_collection_status.csv',
)


def seed_sources(source, destination):
    """Publish bundled public snapshots on each deploy, before children start."""
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in SOURCE_FILES:
        src = source/name
        if not src.is_file():
            raise RuntimeError('Required bundled source snapshot missing: '+name)
    for name in SOURCE_FILES:
        temporary = destination/(name+'.deploy.tmp')
        temporary.write_bytes((source/name).read_bytes())
        temporary.replace(destination/name)
    # Derived reports must be regenerated against this release's source files.
    for name in ('swing_candidates_manifest.json', 'swing_trade_candidates.csv',
                 'final_fundamental_shortlist.csv', 'final_fundamental_screen_summary.csv'):
        (destination/name).unlink(missing_ok=True)


def configure(env, root):
    if not env.get('NSE_DATA_DIR') or root == CODE_ROOT:
        raise RuntimeError('Cloud requires a separate persistent NSE_DATA_DIR')
    if env.get('RENDER') != 'true':
        raise RuntimeError('This launcher requires Render HTTPS ingress')
    env['APP_HOST'] = env.get('APP_HOST') or env.get('RENDER_EXTERNAL_HOSTNAME', '')
    for key in ('APP_HOST', 'APP_SECRET_KEY', 'APP_PASSWORD_HASH', 'APP_TOTP_SECRET'):
        if not env.get(key):
            raise RuntimeError('Missing private deployment setting: '+key)
    if len(env['APP_SECRET_KEY']) < 32:
        raise RuntimeError('APP_SECRET_KEY is too short')
    runtime = root/'runtime'
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    env['APP_AUTH_DB'] = str(runtime/'auth.sqlite')
    env['CLOUD_SUPERVISED'] = '1'
    env['NSE_BOOT_ID'] = str(time.time_ns())
    env['TRUST_SINGLE_PROXY'] = '0'  # Gunicorn handles Render's scheme header.
    env['SMS_ENABLED'] = '0'  # No outbound messages during deployment validation.
    port = int(env.get('PORT', '10000'))
    if not 1 <= port <= 65535:
        raise RuntimeError('Invalid PORT')
    return port


def stop_children(children):
    # Children each own a process group, including worker analysis subprocesses.
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic()+15
    for child in children:
        try:
            child.wait(timeout=max(0.1, deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for child in children:
        child.wait()


def main():
    os.umask(0o077)
    root = data_root()
    env = dict(os.environ)
    port = configure(env, root)
    # Reject a second launcher before it can replace source snapshots.
    lease = sqlite3.connect(root/'runtime'/'cloud_lock.sqlite', timeout=1)
    try:
        lease.execute('BEGIN EXCLUSIVE')
    except sqlite3.OperationalError:
        lease.close()
        raise RuntimeError('Cloud supervisor already running')
    children = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    heartbeat = root/'runtime'/'cloud_health.json'
    try:
        heartbeat.unlink(missing_ok=True)
        seed_sources(CODE_ROOT, root)
        commands = [
            [sys.executable, str(CODE_ROOT/'swing_worker.py')],
            [sys.executable, '-m', 'gunicorn', '--config', str(CODE_ROOT/'gunicorn.conf.py'),
             '--workers', '1', '--bind', f'0.0.0.0:{port}', 'app:create_app()'],
        ]
        for command in commands:
            children.append(subprocess.Popen(command, cwd=CODE_ROOT, env=env, start_new_session=True))
        while not stopping:
            if any(child.poll() is not None for child in children):
                print('A service process exited; restarting the whole instance is required.', flush=True)
                return 1
            atomic_status(heartbeat, {'updated_at': time.time(), 'processes_alive': True,
                                      'child_pids': [child.pid for child in children]})
            time.sleep(2)
        return 0
    finally:
        heartbeat.unlink(missing_ok=True)
        stop_children(children)
        lease.rollback()
        lease.close()


if __name__ == '__main__':
    raise SystemExit(main())
