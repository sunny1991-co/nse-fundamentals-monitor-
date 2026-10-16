"""Fail-closed official NSE equity calendar snapshot for calendar year 2026.

Source: NSE "Market Timings & Holidays" page, checked 16-Oct-2026.
Normal/limited physical market hours: 09:15-15:30 IST.
The page also states that 08-Nov-2026 has Muhurat Trading with timings to be
notified separately; that date is deliberately marked pending rather than
inventing a session.

This is broker-independent. Years without an embedded, reviewed official
snapshot remain unverified.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SOURCE = "https://www.nseindia.com/resources/exchange-communication-holidays?apiversion=v2"
VERIFIED_AT = "2026-10-16"

# Weekday equity trading holidays currently published by NSE for 2026.
HOLIDAYS_2026 = {
    date(2026, 1, 15): "Municipal Corporation Election - Maharashtra",
    date(2026, 1, 26): "Republic Day",
    date(2026, 3, 3): "Holi",
    date(2026, 3, 26): "Shri Ram Navami",
    date(2026, 3, 31): "Shri Mahavir Jayanti",
    date(2026, 4, 3): "Good Friday",
    date(2026, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti",
    date(2026, 5, 1): "Maharashtra Day",
    date(2026, 5, 28): "Bakri Id",
    date(2026, 6, 26): "Muharram",
    date(2026, 9, 14): "Ganesh Chaturthi",
    date(2026, 10, 2): "Mahatma Gandhi Jayanti",
    date(2026, 10, 20): "Dussehra",
    date(2026, 11, 10): "Diwali-Balipratipada",
    date(2026, 11, 24): "Prakash Gurpurb Sri Guru Nanak Dev",
    date(2026, 12, 25): "Christmas",
}

SPECIAL_PENDING_2026 = {
    "2026-11-08": "Muhurat Trading announced by NSE; official session timings pending",
}


def _normal_session(day: date) -> dict[str, str]:
    opening = datetime.combine(day, time(9, 15), IST)
    close = datetime.combine(day, time(15, 30), IST)
    return {"open": opening.isoformat(), "close": close.isoformat()}


def official_equity_calendar(year: int) -> dict:
    if year != 2026:
        return {
            "verified": False,
            "source": SOURCE,
            "valid_from": f"{year:04d}-01-01",
            "valid_to": f"{year:04d}-12-31",
            "sessions": {},
            "reason": "NO_REVIEWED_OFFICIAL_CALENDAR_SNAPSHOT_FOR_YEAR",
        }

    start, end = date(2026, 1, 1), date(2026, 12, 31)
    sessions: dict[str, dict[str, str]] = {}
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        if (
            cursor.weekday() < 5
            and cursor not in HOLIDAYS_2026
            and key not in SPECIAL_PENDING_2026
        ):
            sessions[key] = _normal_session(cursor)
        cursor += timedelta(days=1)

    return {
        "verified": True,
        "source": SOURCE,
        "verified_at": VERIFIED_AT,
        "segment": "NSE_EQUITY_NORMAL_MARKET",
        "valid_from": start.isoformat(),
        "valid_to": end.isoformat(),
        "normal_market_open": "09:15",
        "normal_market_close": "15:30",
        "holiday_dates": {d.isoformat(): reason for d, reason in sorted(HOLIDAYS_2026.items())},
        "special_sessions_pending": SPECIAL_PENDING_2026,
        "sessions": sessions,
    }
