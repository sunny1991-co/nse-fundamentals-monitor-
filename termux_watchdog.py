#!/usr/bin/env python3
"""Independent, low-overhead Termux supervisor heartbeat watchdog.

The supervisor already restarts individual workers. This separate watchdog
ONLY recovers the main supervisor after repeated identity-verified failures.
It cannot survive Android force-stopping the Termux app or reboot by itself.
Termux:Boot can relaunch both after a real device reboot.
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

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status
from research_monitor.termux_resources import memory_snapshot

CODE_ROOT = Path(__file__).resolve().parent
INTERVAL_SECONDS = 120
HEARTBEAT_STALE_SECONDS = 240
CONFIRMATION_GAP_SECONDS = 90
CONFIRMATION_MAX_GAP_SECONDS = 600
RESTART_WINDOW_SECONDS = 6 * 3600
RESTART_COOLDOWN_SECONDS = 30 * 60
MAX_RECOVERIES_PER_WINDOW = 2


def read_json(path: Path) -> dict:
    try:
        if path.is_symlink() or path.stat().st_size > 300_000:
            return {}
        record = json.loads(path.read_text(encoding="utf-8"))
        return record if isinstance(record, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def supervisor_pid(path: Path) -> int | None:
    try:
        if path.is_symlink() or path.stat().st_size > 20:
            return None
        raw = path.read_text(encoding="ascii").strip()
        pid = int(raw)
        return pid if pid > 1 else None
    except (OSError, ValueError, UnicodeError):
        return None


def process_identity(pid: int, proc: Path = Path("/proc")) -> str:
    """UNKNOWN is not repair permission: never signal a reused/unowned PID."""
    try:
        entry = proc / str(pid)
        raw = (entry / "cmdline").read_bytes()
        tokens = [os.fsdecode(t) for t in raw.split(b"\x00") if t]
        if entry.stat().st_uid != os.getuid():
            return "UNVERIFIED"
        if str(CODE_ROOT / "termux_supervisor.py") not in tokens:
            return "UNVERIFIED"
        return "EXPECTED"
    except FileNotFoundError:
        return "NOT_RUNNING"
    except (OSError, PermissionError, ValueError, UnicodeError):
        return "UNVERIFIED"


def supervisor_probe(root: Path, *, epoch: float | None = None,
                     proc: Path = Path("/proc")) -> tuple[str, int | None]:
    epoch = time.time() if epoch is None else epoch
    runtime = root / "runtime"
    pid = supervisor_pid(runtime / "termux_supervisor.pid")
    if pid is None:
        return "SUPERVISOR_MISSING", None
    identity = process_identity(pid, proc=proc)
    if identity == "NOT_RUNNING":
        return "SUPERVISOR_MISSING", pid
    if identity != "EXPECTED":
        return "SUPERVISOR_IDENTITY_UNVERIFIED", pid
    record = read_json(runtime / "termux_supervisor_status.json")
    timestamp = record.get("updated_at")
    if (record.get("supervisor_pid") != pid
        or not isinstance(timestamp, (int, float))
        or isinstance(timestamp, bool)):
        return "SUPERVISOR_STATUS_UNVERIFIED", pid
    age = epoch - float(timestamp)
    if age < -30:
        return "SUPERVISOR_STATUS_UNVERIFIED", pid
    if age <= HEARTBEAT_STALE_SECONDS:
        return "HEALTHY", pid
    return "SUPERVISOR_HEARTBEAT_STALE", pid


def evaluate_recovery(issue: str, journal: dict,
                      *, epoch: float) -> tuple[bool, dict, str]:
    """Two separate checks, rolling restart cap, and rate limit on retries."""
    history = [float(x) for x in journal.get("recoveries", [])
               if isinstance(x, (float, int))
               and not isinstance(x, bool)
               and 0 <= epoch-float(x) <= RESTART_WINDOW_SECONDS]
    old = journal.get("incident", {})
    if not isinstance(old, dict):
        old = {}
    if issue not in {"SUPERVISOR_MISSING", "SUPERVISOR_HEARTBEAT_STALE"}:
        return False, {"incident": {}, "recoveries": history}, "NOT_RECOVERABLE"
    last = old.get("last_checked_epoch")
    try:
        gap = epoch - float(last)
    except (TypeError, ValueError):
        gap = -1
    count = (int(old.get("checks", 0)) + 1
             if old.get("state") == issue
             and CONFIRMATION_GAP_SECONDS <= gap <= CONFIRMATION_MAX_GAP_SECONDS
             else 1)
    incident = {"state": issue, "last_checked_epoch": epoch, "checks": count}
    next_journal = {"incident": incident, "recoveries": history}
    if count < 2:
        return False, next_journal, "AWAITING_SECOND_CONFIRMATION"
    if len(history) >= MAX_RECOVERIES_PER_WINDOW:
        return False, next_journal, "SIX_HOUR_RESTART_CAP_REACHED"
    if history and epoch-history[-1] < RESTART_COOLDOWN_SECONDS:
        return False, next_journal, "RESTART_COOLDOWN"
    next_journal["incident"] = {}
    next_journal["recoveries"].append(epoch)
    return True, next_journal, "SAFE_RESTART_AUTHORIZED"


def recover_supervisor(root: Path, issue: str, pid: int | None,
                       *, proc: Path = Path("/proc")) -> str:
    """Restart only if identity/heartbeat evidence remains valid."""
    runtime = root / "runtime"
    if issue == "SUPERVISOR_HEARTBEAT_STALE":
        if pid is None or process_identity(pid, proc=proc) != "EXPECTED":
            return "RECHECK_IDENTITY_FAILED_NO_SIGNAL"
        confirmed, fresh_pid = supervisor_probe(root, proc=proc)
        if confirmed != "SUPERVISOR_HEARTBEAT_STALE" or fresh_pid != pid:
            return "HEARTBEAT_RECOVERED_NO_SIGNAL"
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except (PermissionError, OSError):
            return "SUPERVISOR_TERMINATION_FAILED"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process_identity(pid, proc=proc) == "NOT_RUNNING":
                break
            time.sleep(1)
        else:
            # Do not SIGKILL a supervisor with active market/archival children.
            return "SUPERVISOR_UNRESPONSIVE_OPERATOR_REQUIRED"
    else:
        new_state, _ = supervisor_probe(root, proc=proc)
        if new_state != "SUPERVISOR_MISSING":
            return "SUPERVISOR_PRESENT_NO_RESTART"
    if not (runtime / "termux_watchdog.enabled").is_file():
        return "EXPLICIT_STOP_NO_RESTART"
    ram = memory_snapshot()
    if not ram["heavy_research_allowed"]:
        return "LOW_MEMORY_NO_RESTART"
    try:
        result = subprocess.run(
            ["bash", str(CODE_ROOT / "termux_backend.sh"), "start"],
            cwd=CODE_ROOT, timeout=35, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "NSE_DATA_DIR": str(root)},
        )
        return "SAFE_START_REQUESTED" if result.returncode == 0 else "START_FAILED"
    except (OSError, subprocess.TimeoutExpired):
        return "START_FAILED"


def main() -> int:
    root = data_root()
    runtime = root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (runtime / "termux_watchdog.lock").open("a+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return 0
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    pid_path = runtime / "termux_watchdog.pid"
    status = runtime / "termux_watchdog_status.json"
    journal_file = runtime / "termux_watchdog_journal.json"
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    os.chmod(pid_path, 0o600)
    try:
        while not stopping and (runtime / "termux_watchdog.enabled").is_file():
            epoch = time.time()
            issue, pid = supervisor_probe(root, epoch=epoch)
            journal = read_json(journal_file)
            allowed, updated, reason = evaluate_recovery(issue, journal, epoch=epoch)
            action = "NONE"
            if allowed:
                # Claim the budget BEFORE an attempt: crashes cannot loop.
                atomic_status(journal_file, updated)
                action = recover_supervisor(root, issue, pid)
            else:
                atomic_status(journal_file, updated)
            atomic_status(status, {
                "checked_at_epoch": time.time(),
                "state": ("HEALTHY" if issue == "HEALTHY" else
                          "RECOVERY_ATTEMPTED" if allowed else
                          "RESTART_LIMITED" if reason in (
                              "SIX_HOUR_RESTART_CAP_REACHED", "RESTART_COOLDOWN")
                          else "OBSERVING"),
                "supervisor_probe": issue,
                "last_action": action,
                "restart_decision": reason,
                "recoveries_in_6h": len(updated["recoveries"]),
                "max_recoveries_per_6h": MAX_RECOVERIES_PER_WINDOW,
                "interval_seconds": INTERVAL_SECONDS,
                "trading_orders": "DISABLED",
            })
            # Sleep in short segments so intentional backend stop exits fast.
            deadline = time.monotonic() + INTERVAL_SECONDS
            while (not stopping and
                   (runtime / "termux_watchdog.enabled").is_file() and
                   time.monotonic() < deadline):
                time.sleep(min(5, max(0.1, deadline - time.monotonic())))
        return 0
    finally:
        try:
            if pid_path.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_path.unlink()
        except (OSError, UnicodeError):
            pass
        atomic_status(status, {
            "checked_at_epoch": time.time(),
            "state": "STOPPED",
            "trading_orders": "DISABLED",
        })
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
