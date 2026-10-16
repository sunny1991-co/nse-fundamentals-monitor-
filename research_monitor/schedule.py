"""Verified exchange sessions, IST boundaries and durable daily research jobs."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')

def phase(calendar, now):
    if now.tzinfo is None:
        raise ValueError('Timezone-aware clock required')
    local = now.astimezone(IST)
    day = local.date().isoformat()
    try:
        if calendar.get('verified') is not True or not calendar.get('source'):
            return 'CALENDAR_PENDING'
        if not calendar['valid_from'] <= day <= calendar['valid_to']:
            return 'CALENDAR_PENDING'
        pending = calendar.get('special_sessions_pending') or {}
        if day in pending:
            return 'CALENDAR_PENDING'
        sessions = calendar['sessions']
        if day not in sessions:
            return 'NON_TRADING_DAY'
        session = sessions[day]
        opening = datetime.fromisoformat(session['open'])
        close = datetime.fromisoformat(session['close'])
        if opening.tzinfo is None or close.tzinfo is None or opening >= close:
            return 'CALENDAR_PENDING'
        if opening.astimezone(IST).date().isoformat() != day or close.astimezone(IST).date().isoformat() != day:
            return 'CALENDAR_PENDING'
        if now < opening:
            return 'PRE_MARKET'
        if now < close:
            return 'LIVE_MONITORING'
        return 'AFTER_MARKET_ANALYSIS'
    except (KeyError, TypeError, ValueError):
        return 'CALENDAR_PENDING'

def atomic_status(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temp.replace(path)

def claim_job(path, day, job):
    """At most once automatically per day, including after restarts/crashes."""
    with sqlite3.connect(path, timeout=10) as db:
        db.execute('CREATE TABLE IF NOT EXISTS jobs(day TEXT,job TEXT,status TEXT,PRIMARY KEY(day,job))')
        return bool(db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?)', (day, job, 'STARTED')).rowcount)
