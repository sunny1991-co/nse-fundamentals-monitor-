"""Once-per-IST-day, post-market, read-only Upstox historical reference worker.

Supervised by Termux. Hard cap: one automatic invocation of <=25 new issuers
per calendar day, regardless of restart or mid-batch crash (SQLite claim is
committed BEFORE any network calls). Completed per-symbol reference records are
checkpointed by upstox_reference_batches.py.

This is NOT live quotes, NSE audited XBRL/official sector verification,
Groww execution, investment selection, or authorization of orders.

A stopped/temporarily offline device simply misses that day's opportunity;
the next day, if backend/network are available, it resumes outstanding
vendor-reference issuers from their durable private records.
"""
from __future__ import annotations

import argparse
from datetime import datetime, time as dt_time, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import time

from dotenv import load_dotenv

from research_monitor.paths import CODE_ROOT, data_root
from research_monitor.schedule import IST, atomic_status, claim_job
from research_monitor.upstox_instrument_master import (
    InstrumentMapUnavailable, METADATA_NAME, sync as sync_instruments,
)
from upstox_reference_batches import (
    MAX_BATCH, MAX_SOURCE_AGE_DAYS, ReferenceBatchError,
    _source_dir, collect, verified_reference_map,
)

load_dotenv(CODE_ROOT / ".env", interpolate=False)

DAILY_JOB = "UPSTOX_NSE_EQ_VENDOR_REFERENCE_25_READ_ONLY_V1"
CHECK_INTERVAL_SECONDS = 30 * 60
# Official NSE cash normally closes 15:30 IST. Research collection is delayed
# until 17:00 IST, and is not tied to exchange session/approval readiness.
START_LOCAL = dt_time(17, 0)
END_LOCAL = dt_time(23, 50)
STATUS_NAME = "upstox_reference_worker_status.json"
JOBS_NAME = "upstox_reference_daily_jobs.sqlite"


class DailyReferenceError(RuntimeError):
    pass


def _status_path(root):
    return Path(root) / "runtime" / STATUS_NAME


def _jobs_path(root):
    return Path(root) / "runtime" / JOBS_NAME


def _status(root, state, now=None, **fields):
    now = now or datetime.now(timezone.utc)
    data = {
        "scope": "NSE_EQ_UPSTOX_SECONDARY_VENDOR_AUTOMATIC_REFERENCE_ONLY",
        "updated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "state": state,
        "daily_max_new_company_attempts": MAX_BATCH,
        "start_after_ist": "17:00",
        "execution_broker_priority": "GROWW_PRIMARY_ONLY_NO_ORDERS",
        "upstox_trade_permissions": "READ_ONLY_DATA_REFERENCE",
        "nse_audited_verified_changes": 0,
        "official_nse_sector_changes": 0,
        "strict_fundamental_pass_changes": 0,
        "trading_strategy_approved": False,
        "orders": "DISABLED",
        **fields,
    }
    atomic_status(_status_path(root), data)
    try:
        os.chmod(_status_path(root), 0o600)
    except OSError:
        pass
    return data


def _day_claimed(root, day):
    """Read the durable, local SQLite claim without creating/resetting it."""
    path = _jobs_path(root)
    if not path.is_file():
        return False
    try:
        with sqlite3.connect(path, timeout=5) as db:
            row = db.execute(
                "SELECT 1 FROM jobs WHERE day=? AND job=?",
                (day, DAILY_JOB),
            ).fetchone()
            return row is not None
    except sqlite3.Error:
        # Do not permit extra automatic runs when the daily ledger is broken.
        raise DailyReferenceError("DAILY_JOB_LEDGER_UNREADABLE") from None


def _refresh_only_expired_mapping(root, now):
    """Fail closed if provenance is missing/broken or metadata time is future.

    Public (non-token) instrument master is refetched only when the trusted
    map is known to be older than seven days; any ISIN changes in already
    checkpointed companies still trigger a manual audit on collection.
    """
    root=Path(root)
    try:
        return verified_reference_map(root, now=now)
    except ReferenceBatchError as exc:
        message = str(exc)
        if message != "UPSTOX_MAP_STALE_OR_FUTURE_DATE":
            raise
        path = _source_dir(root) / METADATA_NAME
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            stamp = datetime.fromisoformat(
                obj["fetched_at_utc"].replace("Z", "+00:00")
            )
            if stamp.tzinfo is None:
                raise ValueError("NAIVE_TIMESTAMP")
            age = now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)
        except (OSError, KeyError, TypeError, ValueError, AttributeError):
            raise ReferenceBatchError("UPSTOX_MAPPING_DATE_NOT_VERIFIED") from None
        if age <= timedelta(days=MAX_SOURCE_AGE_DAYS):
            raise ReferenceBatchError("UPSTOX_MAPPING_CLOCK_OR_PROVENANCE_ERROR")
        # One public GET; does not send Analytics Token to public file host.
        sync_instruments(root)
        return verified_reference_map(root, now=now)


def run_daily(root, now=None, *, token=None, max_batch=MAX_BATCH,
              collector=collect, refresh_mapping=_refresh_only_expired_mapping):
    """Single safe daily attempt. Can be tested with all network dependencies mocked.

    Runs after 17:00 IST; persistent claim is acquired before public BOD
    refresh OR Upstox fundamentals. Never retries on restart in the same day.
    """
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    if not isinstance(max_batch, int) or isinstance(max_batch, bool) or not (
        1 <= max_batch <= MAX_BATCH
    ):
        raise ValueError("DAILY_BATCH_MUST_BE_1_TO_25")
    local = now.astimezone(IST)
    day = local.date().isoformat()

    if not START_LOCAL <= local.time() < END_LOCAL:
        return _status(root, "WAITING_POST_MARKET_IST", now, ist_day=day)
    if _day_claimed(root, day):
        # Preserve a completed/blocked run's status across supervisor checks.
        # A crash between the SQLite claim and final status does NOT retry.
        try:
            previous=json.loads(_status_path(root).read_text(encoding="utf-8"))
            if (
                previous.get("ist_day")==day
                and previous.get("scope")==
                    "NSE_EQ_UPSTOX_SECONDARY_VENDOR_AUTOMATIC_REFERENCE_ONLY"
                and previous.get("state") not in (
                    "STARTED_READ_ONLY_DAILY_BATCH",
                    "WAITING_POST_MARKET_IST",
                    "WAITING_UPSTOX_ANALYTICS_TOKEN",
                )
            ):
                return previous
        except (OSError,ValueError,TypeError,AttributeError):
            pass
        return _status(
            root, "ALREADY_CLAIMED_OUTCOME_UNCERTAIN_NO_RETRY_TODAY",
            now, ist_day=day,
        )
    if not isinstance(token, str) or not token.strip():
        return _status(
            root, "WAITING_UPSTOX_ANALYTICS_TOKEN", now, ist_day=day,
            token_present=False,
        )

    _jobs_path(root).parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        claimed = claim_job(_jobs_path(root), day, DAILY_JOB)
    except sqlite3.Error:
        return _status(root, "DAILY_JOB_LEDGER_UNAVAILABLE", now, ist_day=day)
    if not claimed:
        return _status(root, "ALREADY_ATTEMPTED_TODAY", now, ist_day=day)

    _status(root,"STARTED_READ_ONLY_DAILY_BATCH",now,ist_day=day,
            token_present=True,claimed_before_network=True)

    try:
        refresh_mapping(root, now)
        result = collector(root, token, max_batch)
        fatal=result.get("stop_reason")
        state = (
            "STOPPED_ON_VENDOR_API_CONSTRAINT" if fatal
            else "REFERENCE_UNIVERSE_CURRENTLY_COMPLETE"
            if result.get("remaining_uncollected") == 0
            else "COMPLETED_DAILY_REFERENCE_BATCH"
        )
        return _status(
            root,state,datetime.now(timezone.utc),ist_day=day,
            token_present=True,
            new_company_records=result.get("new_company_records",0),
            completed_company_records=result.get("completed_company_records",0),
            remaining_uncollected=result.get("remaining_uncollected"),
            mapping_symbols=result.get("mapping_symbols"),
            requests_this_run=result.get("requests_this_run",0),
            stop_reason=fatal,
            private_batch_report=str(_source_dir(root) /
                                     "upstox-reference-batches-v1" /
                                     "last_batch.json"),
        )
    except (ReferenceBatchError,InstrumentMapUnavailable) as exc:
        # Exception text from these modules is a fixed local error-code, not
        # a broker response, URL token or response body. Still restrict logs
        # to fixed characters for defense in depth.
        code=str(exc)
        if not code.isascii() or not (
            1 <= len(code) <= 100 and
            all(c.isupper() or c.isdigit() or c=="_" for c in code)
        ):
            code="VENDOR_REFERENCE_ERROR"
        return _status(
            root,"STOPPED_ON_SOURCE_OR_MAPPING_GUARD",now,
            ist_day=day,token_present=True,error_code=code,
        )
    except (OSError,ValueError,TypeError,RuntimeError):
        # Unexpected errors never include exception strings (secrets).
        return _status(
            root,"STOPPED_ON_LOCAL_ERROR",now,ist_day=day,
            token_present=True,error_code="LOCAL_RUNTIME_ERROR",
        )


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once",action="store_true",
                        help="Try at most one IST-day auto batch then exit")
    parser.add_argument("--status",action="store_true",
                        help="Print last private worker status without running a batch")
    args=parser.parse_args(argv)
    root=data_root()
    runtime=root/"runtime"
    runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
    if args.status:
        try:
            print(_status_path(root).read_text(encoding="utf-8"))
        except OSError:
            print('{"state":"NO_DAILY_REFERENCE_WORKER_STATUS"}')
        return 0

    lock = (runtime / "upstox_reference_worker.lock").open("a+")
    os.chmod(runtime/"upstox_reference_worker.lock",0o600)
    try:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print("Upstox reference worker already running (no extra instance).")
        lock.close()
        return 2

    stopping=False
    def stop(_sig,_frame):
        nonlocal stopping
        stopping=True

    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        while not stopping:
            # Source secrets remain in process-private environment; no token
            # bytes are written to status files, logs, URLs or CLI arguments.
            status=run_daily(
                root,token=os.environ.get("UPSTOX_ANALYTICS_TOKEN","")
            )
            # Only fixed state codes, NO tokens, company data or URLs printed.
            print("NSE EQ read-only vendor worker:",status["state"],flush=True)
            if args.once:
                break
            deadline=time.monotonic()+CHECK_INTERVAL_SECONDS
            while not stopping and time.monotonic()<deadline:
                time.sleep(min(10,max(1,deadline-time.monotonic())))
        return 0
    finally:
        # Do not overwrite the durable last successful/blocked batch outcome
        # with a generic STOPPED state when --once or supervisor shutdown exits.
        fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
        lock.close()


if __name__=="__main__":
    raise SystemExit(main())
