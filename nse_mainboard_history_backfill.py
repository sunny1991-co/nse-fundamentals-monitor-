#!/usr/bin/env python3
"""Resumable, separate, UNADJUSTED NSE EQ archive for new-company research.

- Official daily bhavcopies; saves *all EQ rows* so new listed symbols don't
  require old processed archives to be rescanned when catalog membership moves.
- A current, verified mainboard catalog is required before collecting.
- Uses an independent SQLite file, NEVER the adjusted trade research files.
- Historical symbol membership / corporate actions are not attested here.
- This raw archive is forbidden as a live-entry, backtest or broker order feed.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import fcntl
import json
from pathlib import Path
import sqlite3
import time

import requests

from nse_mainboard_discovery import STATUS as DISCOVERY_STATUS
from research_monitor.discovered_annual_universe import verified_discovered_symbols
from research_monitor.history import COLUMNS
from research_monitor.nse_history_calendar import expected_archive_day
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status
from swing_nse_history import fetch_one_day

DATABASE = Path("runtime/mainboard_raw_research_history.sqlite")
STATUS = Path("runtime/mainboard_raw_research_history_status.json")
LOCK = Path("runtime/mainboard_raw_research_history.lock")
BEGIN = date(2020, 1, 1)
MAX_DAYS = 100
MAX_MINUTES = 15
MAX_ABSENT_RETRIES = 3
MAX_ROWS_PER_DAY = 10000


class BackfillBlocked(RuntimeError):
    pass


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path, timeout=15)
    db.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS source_days(
        day TEXT PRIMARY KEY, status TEXT NOT NULL,
        source_url TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        record_count INTEGER NOT NULL, attempts INTEGER NOT NULL,
        checked_at_utc TEXT NOT NULL, error TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS unadjusted_eq_bars(
        symbol TEXT NOT NULL, day TEXT NOT NULL,
        open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
        close REAL NOT NULL, volume REAL NOT NULL,
        source_url TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        PRIMARY KEY(symbol,day)
    );
    """)
    return db


def missing_days(db: sqlite3.Connection, start: date, end: date) -> list[date]:
    old = {
        day: (state, tries)
        for day, state, tries in db.execute(
            "SELECT day,status,attempts FROM source_days WHERE day BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        )
    }
    pending = []
    current = end
    while current >= start:
        if expected_archive_day(current):
            state, tries = old.get(current.isoformat(), ("", 0))
            if state != "OK" and not (
                state == "NO_FILE" and int(tries) >= MAX_ABSENT_RETRIES
            ):
                pending.append(current)
        current -= timedelta(days=1)
    return pending


def store_day(db: sqlite3.Connection, day: date, kind: str, url: str,
              digest: str, rows: list[tuple], error: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if kind not in ("OK", "NO_FILE", "BLOCKED"):
        raise ValueError("UNKNOWN_EXCHANGE_FETCH_STATE")
    if kind == "OK" and (
        not url.startswith("https://nsearchives.nseindia.com/")
        or len(digest) != 64 or not 1 <= len(rows) <= MAX_ROWS_PER_DAY
        or any(row[1] != day.isoformat() for row in rows)
        or any(not row[0] for row in rows)
    ):
        raise BackfillBlocked("UNTRUSTED_EXCHANGE_DAY_FILE")
    previous = db.execute(
        "SELECT attempts FROM source_days WHERE day=?", (day.isoformat(),)
    ).fetchone()
    tries = int(previous[0] or 0) + 1 if previous else 1
    # Atomic day + all rows: never label the day complete until all bars exist.
    with db:
        if kind == "OK":
            db.executemany(
                """INSERT OR REPLACE INTO unadjusted_eq_bars
                (symbol,day,open,high,low,close,volume,source_url,source_sha256)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                [(*row, url, digest) for row in rows],
            )
        db.execute(
            """INSERT OR REPLACE INTO source_days
            (day,status,source_url,source_sha256,record_count,attempts,checked_at_utc,error)
            VALUES(?,?,?,?,?,?,?,?)""",
            (day.isoformat(),kind,url,digest,len(rows),tries,stamp,error[:300]),
        )


def collect(root: Path, *, start: date = BEGIN, end: date | None = None,
            max_days: int = 40, max_minutes: int = 12, session=None,
            fetch=fetch_one_day, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    if not 1 <= max_days <= MAX_DAYS or not 1 <= max_minutes <= MAX_MINUTES:
        raise ValueError("BOUNDED_REQUEST_1_TO_MAX_DAYS_AND_MINUTES_REQUIRED")
    latest_completed = now.astimezone(timezone.utc).date() - timedelta(days=1)
    # NSE-local date. UTC at 01:00 is already a new IST date, but never
    # request a current Indian market session's daily archive.
    from research_monitor.schedule import IST
    latest_completed = now.astimezone(IST).date() - timedelta(days=1)
    end = min(end or latest_completed, latest_completed)
    if start < BEGIN or start > end:
        raise ValueError("INVALID_COMPLETED_HISTORICAL_RANGE")
    root = Path(root)
    current_symbols = verified_discovered_symbols(root, now=now)
    if not current_symbols:
        raise BackfillBlocked("NO_VERIFIED_MAINBOARD_UNIVERSE")
    lock_path = root / LOCK
    lock_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackfillBlocked("ANOTHER_MAINBOARD_BACKFILL_RUNNING") from None
        db = open_db(root / DATABASE)
        own_session = session is None
        session = session or requests.Session()
        attempted = completed = raw_rows = unavailable = 0
        blocked = False
        state = "RESEARCH_UNADJUSTED_EOD_PROGRESS"
        try:
            to_do = missing_days(db,start,end)
            deadline = time.monotonic() + max_minutes * 60
            for day in to_do[:max_days]:
                if time.monotonic() >= deadline:
                    break
                kind,url,digest,rows,error = fetch(session,day,None)
                attempted += 1
                if kind=="BLOCKED":
                    # No bypass/proxy/retry after NSE access control refusal.
                    blocked = True
                    state = "NSE_ACCESS_BLOCKED"
                    break
                if kind == "NO_FILE":
                    unavailable += 1
                    store_day(db,day,kind,url,digest,[],error)
                    if unavailable>=5:
                        state = "ARCHIVE_MISSING_BATCH_REVIEW"
                        break
                    continue
                if kind!="OK":
                    state = "UNEXPECTED_SOURCE_RESULT"
                    break
                store_day(db,day,kind,url,digest,rows,error)
                completed += 1
                raw_rows += len(rows)
            left = len(missing_days(db,start,end))
            no_file = db.execute(
                "SELECT count(*) FROM source_days WHERE status='NO_FILE'"
            ).fetchone()[0]
            archive_ok = db.execute(
                "SELECT count(*) FROM source_days WHERE status='OK'"
            ).fetchone()[0]
            report = {
                "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
                "state":state,
                "archive_period_start":start.isoformat(),
                "archive_period_end":end.isoformat(),
                "current_mainboard_eq_members":len(current_symbols),
                "archive_days_attempted_this_run":attempted,
                "archive_days_added_this_run":completed,
                "unadjusted_eq_bars_added_or_replaced_this_run":raw_rows,
                "all_complete_archive_days":archive_ok,
                "unverified_no_file_day_records":no_file,
                "estimated_outstanding_expected_sessions":left,
                "source_blocked":blocked,
                "read_only_research_data_not_adjusted_for_corporate_actions":True,
                "historic_membership_not_attested":True,
                "feeds_trading_watchlist":False,
                "feeds_live_orders":False,
                "orders":"DISABLED",
            }
            atomic_status(root/STATUS,report)
            return report
        finally:
            if own_session:
                session.close()
            db.close()
            fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days",type=int,default=40)
    p.add_argument("--max-minutes",type=int,default=12)
    p.add_argument("--start",default=BEGIN.isoformat())
    p.add_argument("--end",default=None)
    a=p.parse_args()
    try:
        report=collect(
            data_root(),start=date.fromisoformat(a.start),
            end=date.fromisoformat(a.end) if a.end else None,
            max_days=a.days,max_minutes=a.max_minutes,
        )
    except (BackfillBlocked, ValueError, OSError, sqlite3.Error,
            requests.RequestException) as exc:
        report={"checked_at_utc":datetime.now(timezone.utc).isoformat(),
                "state":"MAINBOARD_RESEARCH_BACKFILL_BLOCKED",
                "reason":str(exc)[:200],"prior_archive_preserved":True,
                "orders":"DISABLED"}
        atomic_status(data_root()/STATUS,report)
        print(json.dumps(report,indent=2))
        return 1
    print(json.dumps(report,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
