"""Termux process supervisor for the read-only swing worker.

It keeps one worker alive, holds an Android wake lock when the command exists,
and never places broker orders. Android may still stop Termux if the OS/user
force-stops it, so battery restrictions and Termux:Boot remain operator setup.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from dotenv import load_dotenv

CODE_ROOT = Path(__file__).resolve().parent
load_dotenv(CODE_ROOT/'.env', interpolate=False)

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status


def acquire_wakelock(run=subprocess.run, which=shutil.which) -> bool:
    command = which('termux-wake-lock')
    if not command:
        return False
    try:
        result = run([command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     timeout=10, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def release_wakelock(run=subprocess.run, which=shutil.which) -> None:
    command = which('termux-wake-unlock')
    if not command:
        return
    try:
        run([command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def restart_delay(restarts: list[float], now: float) -> tuple[list[float], int]:
    recent = [stamp for stamp in restarts if now-stamp <= 300]
    recent.append(now)
    return recent, (60 if len(recent) >= 6 else 5)


def terminate_child(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def main() -> int:
    root = data_root()
    runtime = root/'runtime'
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)

    lock_path = runtime/'termux_supervisor.lock'
    lock = lock_path.open('a+')
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('Termux supervisor already running.', flush=True)
        lock.close()
        return 2

    pid_path = runtime/'termux_supervisor.pid'
    status_path = runtime/'termux_supervisor_status.json'
    log_path = runtime/'termux_backend.log'
    pid_path.write_text(str(os.getpid()), encoding='ascii')
    os.chmod(pid_path, 0o600)

    stopping = False
    child = None

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    wake_lock = acquire_wakelock()
    restarts: list[float] = []
    try:
        while not stopping:
            with log_path.open('a', encoding='utf-8', buffering=1) as log:
                started = time.time()
                log.write(f'\n{time.strftime("%Y-%m-%dT%H:%M:%S%z")} starting swing_worker.py\n')
                child = subprocess.Popen(
                    [sys.executable, str(CODE_ROOT/'swing_worker.py')],
                    cwd=CODE_ROOT,
                    env=dict(os.environ),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                atomic_status(status_path, {
                    'updated_at': time.time(),
                    'supervisor_pid': os.getpid(),
                    'worker_pid': child.pid,
                    'state': 'RUNNING',
                    'wake_lock': wake_lock,
                    'orders': 'MANUAL_ONLY',
                })
                while child.poll() is None and not stopping:
                    atomic_status(status_path, {
                        'updated_at': time.time(),
                        'supervisor_pid': os.getpid(),
                        'worker_pid': child.pid,
                        'state': 'RUNNING',
                        'wake_lock': wake_lock,
                        'orders': 'MANUAL_ONLY',
                    })
                    time.sleep(10)
                if stopping:
                    terminate_child(child)
                    break
                exit_code = child.returncode
                restarts, delay = restart_delay(restarts, time.monotonic())
                log.write(f'{time.strftime("%Y-%m-%dT%H:%M:%S%z")} worker exited '
                          f'code={exit_code}; restart in {delay}s\n')
                atomic_status(status_path, {
                    'updated_at': time.time(),
                    'supervisor_pid': os.getpid(),
                    'worker_pid': None,
                    'state': 'RESTART_WAIT',
                    'last_exit_code': exit_code,
                    'restart_delay_seconds': delay,
                    'wake_lock': wake_lock,
                    'orders': 'MANUAL_ONLY',
                })
            deadline = time.monotonic()+delay
            while not stopping and time.monotonic() < deadline:
                time.sleep(1)
        return 0
    finally:
        if child is not None:
            terminate_child(child)
        release_wakelock()
        try:
            pid_path.unlink()
        except FileNotFoundError:
            pass
        atomic_status(status_path, {
            'updated_at': time.time(),
            'supervisor_pid': os.getpid(),
            'worker_pid': None,
            'state': 'STOPPED',
            'wake_lock': False,
            'orders': 'MANUAL_ONLY',
        })
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
