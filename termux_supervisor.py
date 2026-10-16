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


def history_command() -> list[str]:
    return [sys.executable, str(CODE_ROOT/'termux_history_worker.py')]


def research_command() -> list[str]:
    return [sys.executable, str(CODE_ROOT/'termux_research_worker.py')]


def dashboard_env(base: dict) -> dict:
    env = dict(base)
    env['LOCAL_LOOPBACK_ONLY'] = '1'
    env['APP_HOST'] = 'localhost'
    env['TRUST_SINGLE_PROXY'] = '0'
    env['SMS_ENABLED'] = '0'
    return env


def cleanup_stale_dashboard_processes(proc_root: Path = Path('/proc')) -> list[int]:
    """Stop only same-user orphan gunicorn masters bound to our loopback port."""
    killed = []
    try:
        own_uid = os.getuid()
        entries = list(proc_root.iterdir())
    except (OSError, PermissionError):
        return killed
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != own_uid:
                continue
            raw = (entry/'cmdline').read_bytes()
            cmd = raw.replace(b'\x00', b' ').decode('utf-8', errors='ignore')
        except (OSError, PermissionError):
            continue
        if (
            'gunicorn' in cmd
            and '127.0.0.1:8787' in cmd
            and 'app:create_app()' in cmd
        ):
            pid = int(entry.name)
            if pid == os.getpid():
                continue
            try:
                os.killpg(pid, signal.SIGTERM)
                killed.append(pid)
            except (ProcessLookupError, PermissionError):
                continue
    if killed:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            alive = []
            for pid in killed:
                try:
                    os.kill(pid, 0)
                    alive.append(pid)
                except (ProcessLookupError, PermissionError):
                    pass
            if not alive:
                break
            time.sleep(0.2)
        for pid in killed:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                continue
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    return killed


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
    """Stop all child process groups in parallel within one shared deadline."""
    active = [child for child in children.values() if child.poll() is None]
    for child in active:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + 15
    for child in active:
        remaining = max(0.1, deadline-time.monotonic())
        try:
            child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            pass

    for child in active:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    for child in active:
        if child.poll() is None:
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

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    wake_lock = acquire_wakelock()
    stale_dashboards = cleanup_stale_dashboard_processes()
    restarts: list[float] = []
    children: dict[str, subprocess.Popen] = {}
    try:
        while not stopping:
            base_env = dict(os.environ)
            mode = dashboard_mode(base_env)
            child_env = dashboard_env(base_env)
            with log_path.open('a', encoding='utf-8', buffering=1) as log:
                log.write(f'\n{time.strftime("%Y-%m-%dT%H:%M:%S%z")} starting swing_worker.py\n')
                def start_named(name):
                    if name == 'worker':
                        command = [sys.executable, str(CODE_ROOT/'swing_worker.py')]
                        env = base_env
                    elif name == 'history':
                        command = history_command()
                        env = base_env
                    elif name == 'research':
                        command = research_command()
                        env = base_env
                    elif name == 'dashboard':
                        command = dashboard_command(base_env)
                        env = child_env
                        if not command:
                            return None
                    else:
                        raise ValueError('Unknown supervised process: '+name)
                    log.write(f'{time.strftime("%Y-%m-%dT%H:%M:%S%z")} starting {name}\n')
                    log.flush()
                    return subprocess.Popen(
                        command, cwd=CODE_ROOT, env=env, stdout=log,
                        stderr=subprocess.STDOUT, start_new_session=True,
                    )

                children = {}
                for name in ('worker', 'history', 'research', 'dashboard'):
                    child = start_named(name)
                    if child is not None:
                        children[name] = child

                def publish(state='RUNNING', **extra):
                    atomic_status(status_path, {
                        'updated_at': time.time(),
                        'supervisor_pid': os.getpid(),
                        'worker_pid': children.get('worker').pid if children.get('worker') else None,
                        'history_pid': children.get('history').pid if children.get('history') else None,
                        'research_pid': children.get('research').pid if children.get('research') else None,
                        'dashboard_pid': children.get('dashboard').pid if children.get('dashboard') else None,
                        'dashboard': mode,
                        'dashboard_url': 'http://localhost:8787' if 'dashboard' in children else None,
                        'state': state,
                        'wake_lock': wake_lock,
                        'orders': 'MANUAL_ONLY',
                        'stale_dashboards_cleaned': stale_dashboards,
                        **extra,
                    })

                publish()
                last_publish = time.monotonic()
                restart_history: dict[str, list[float]] = {}
                while not stopping:
                    failed_name = next(
                        (name for name, child in children.items() if child.poll() is not None),
                        None,
                    )
                    if failed_name:
                        failed = children[failed_name]
                        exit_code = failed.returncode
                        history = restart_history.get(failed_name, [])
                        history, delay = restart_delay(history, time.monotonic())
                        restart_history[failed_name] = history
                        log.write(
                            f'{time.strftime("%Y-%m-%dT%H:%M:%S%z")} {failed_name} exited '
                            f'code={exit_code}; restart in {delay}s\n'
                        )
                        log.flush()
                        publish(
                            'PROCESS_RESTART_WAIT',
                            last_failed_process=failed_name,
                            last_exit_code=exit_code,
                            restart_delay_seconds=delay,
                        )
                        deadline = time.monotonic()+delay
                        while not stopping and time.monotonic() < deadline:
                            time.sleep(1)
                        if stopping:
                            break
                        replacement = start_named(failed_name)
                        if replacement is None:
                            children.pop(failed_name, None)
                        else:
                            children[failed_name] = replacement
                        publish('RUNNING', last_restarted_process=failed_name)
                        last_publish = time.monotonic()
                        continue

                    now_tick = time.monotonic()
                    if now_tick-last_publish >= 10:
                        publish()
                        last_publish = now_tick
                    time.sleep(1)

                if stopping:
                    terminate_all(children)
                    break
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
            'research_pid': None,
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
