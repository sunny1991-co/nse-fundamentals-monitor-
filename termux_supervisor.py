"""Termux supervisor for swing research, NSE history catch-up, and dashboard.

No broker order route exists. The dashboard is started only after password +
Authenticator enrollment has produced all required private settings.
"""
from __future__ import annotations

import fcntl
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

AUTH_KEYS = ('APP_SECRET_KEY','APP_PASSWORD_HASH','APP_TOTP_SECRET','APP_AUTH_DB')


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


def dashboard_mode(env: dict) -> str:
    present = [bool(env.get(key)) for key in AUTH_KEYS]
    if not any(present):
        return 'NOT_ENROLLED'
    if not all(present):
        return 'CONFIG_INCOMPLETE'
    return 'LOOPBACK_MFA_ENABLED'


def dashboard_command(env: dict) -> list[str] | None:
    if dashboard_mode(env) != 'LOOPBACK_MFA_ENABLED':
        return None
    return [sys.executable, '-m', 'gunicorn', '--workers', '1', '--bind',
            '127.0.0.1:8787', 'app:create_app()']


def dashboard_env(base: dict) -> dict:
    env = dict(base)
    env['LOCAL_LOOPBACK_ONLY'] = '1'
    env['APP_HOST'] = 'localhost'
    env['TRUST_SINGLE_PROXY'] = '0'
    env['SMS_ENABLED'] = '0'
    return env


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


def terminate_all(children: dict[str, subprocess.Popen]) -> None:
    for child in children.values():
        terminate_child(child)


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

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    wake_lock = acquire_wakelock()
    restarts: list[float] = []
    children: dict[str, subprocess.Popen] = {}
    try:
        while not stopping:
            base_env = dict(os.environ)
            mode = dashboard_mode(base_env)
            child_env = dashboard_env(base_env)
            with log_path.open('a', encoding='utf-8', buffering=1) as log:
                log.write(f'\n{time.strftime("%Y-%m-%dT%H:%M:%S%z")} starting swing_worker.py\n')
                children = {
                    'worker': subprocess.Popen(
                        [sys.executable, str(CODE_ROOT/'swing_worker.py')],
                        cwd=CODE_ROOT, env=base_env, stdout=log,
                        stderr=subprocess.STDOUT, start_new_session=True,
                    ),
                    'history': subprocess.Popen(
                        [sys.executable, str(CODE_ROOT/'termux_history_worker.py')],
                        cwd=CODE_ROOT, env=base_env, stdout=log,
                        stderr=subprocess.STDOUT, start_new_session=True,
                    ),
                }
                command = dashboard_command(base_env)
                if command:
                    log.write(f'{time.strftime("%Y-%m-%dT%H:%M:%S%z")} starting loopback dashboard\n')
                    children['dashboard'] = subprocess.Popen(
                        command, cwd=CODE_ROOT, env=child_env, stdout=log,
                        stderr=subprocess.STDOUT, start_new_session=True,
                    )

                def publish(state='RUNNING', **extra):
                    atomic_status(status_path, {
                        'updated_at': time.time(),
                        'supervisor_pid': os.getpid(),
                        'worker_pid': children.get('worker').pid if children.get('worker') else None,
                        'history_pid': children.get('history').pid if children.get('history') else None,
                        'dashboard_pid': children.get('dashboard').pid if children.get('dashboard') else None,
                        'dashboard': mode,
                        'dashboard_url': 'http://localhost:8787' if 'dashboard' in children else None,
                        'state': state,
                        'wake_lock': wake_lock,
                        'orders': 'MANUAL_ONLY',
                        **extra,
                    })

                publish()
                failed_name = None
                while not stopping:
                    failed_name = next((name for name, child in children.items()
                                        if child.poll() is not None), None)
                    if failed_name:
                        break
                    publish()
                    time.sleep(10)

                if stopping:
                    terminate_all(children)
                    break

                exit_code = children[failed_name].returncode if failed_name else None
                terminate_all(children)
                restarts, delay = restart_delay(restarts, time.monotonic())
                log.write(f'{time.strftime("%Y-%m-%dT%H:%M:%S%z")} {failed_name} exited '
                          f'code={exit_code}; restart in {delay}s\n')
                publish('RESTART_WAIT', last_failed_process=failed_name,
                        last_exit_code=exit_code, restart_delay_seconds=delay)
            deadline = time.monotonic()+delay
            while not stopping and time.monotonic() < deadline:
                time.sleep(1)
        return 0
    finally:
        terminate_all(children)
        release_wakelock()
        try:
            pid_path.unlink()
        except FileNotFoundError:
            pass
        atomic_status(status_path, {
            'updated_at': time.time(),
            'supervisor_pid': os.getpid(),
            'worker_pid': None,
            'history_pid': None,
            'dashboard_pid': None,
            'dashboard': dashboard_mode(dict(os.environ)),
            'dashboard_url': None,
            'state': 'STOPPED',
            'wake_lock': False,
            'orders': 'MANUAL_ONLY',
        })
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
