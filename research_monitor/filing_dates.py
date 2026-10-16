"""Deterministic NSE dates: ISO dates must never be interpreted as day-first."""
import re
from datetime import date, datetime

QUARTERLY_PARSER_VERSION = "6"


def parse_filing_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if re.match(r"^\d{4}-", text):
        try:
            # ISO invalid months stay invalid; no fallback can swap month/day.
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%d/%m/%Y",
                "%d-%b-%Y %H:%M:%S", "%d-%B-%Y %H:%M:%S",
                "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None
