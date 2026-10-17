"""Verified current NSE mainboard symbol source for annual XBRL catch-up.

Membership is not a fundamental pass, historical constituent attestation,
trade signal or authority to overwrite existing baseline CSVs.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

from nse_mainboard_discovery import CATALOG, STATUS, EQUITY_URL, SME_URL

MAX_AGE_HOURS = 48
MIN_MAINBOARD = 1000
MAX_MAINBOARD = 5000
SYMBOL = re.compile(r"^[A-Z0-9&._-]{1,40}$")
ISIN = re.compile(r"^IN[A-Z0-9]{10}$")


class DiscoveredUniverseUnavailable(RuntimeError):
    pass


def verified_discovered_symbols(root: Path, now: datetime | None = None) -> set[str]:
    """Require the *current official* EQ catalog and status from the same run."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    root = Path(root)
    try:
        meta = json.loads((root / STATUS).read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(meta["checked_at_utc"])
        if (stamp.tzinfo is None or not timedelta(0) <=
                now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)
                <= timedelta(hours=MAX_AGE_HOURS)):
            raise DiscoveredUniverseUnavailable("DISCOVERY_OLDER_THAN_48_HOURS")
        count = meta.get("mainboard_eq_count")
        if (meta.get("state") != "OFFICIAL_DISCOVERY_UPDATED" or
                meta.get("equity_url") != EQUITY_URL or
                meta.get("sme_url") != SME_URL or
                not isinstance(count, int) or isinstance(count, bool) or
                not MIN_MAINBOARD <= count <= MAX_MAINBOARD):
            raise DiscoveredUniverseUnavailable("DISCOVERY_NOT_OFFICIALLY_VERIFIED")
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError):
        raise DiscoveredUniverseUnavailable("DISCOVERY_STATUS_MISSING_OR_INVALID") from None

    path = root / CATALOG
    symbols, isins = set(), set()
    try:
        if not path.is_file() or path.stat().st_size > 2_000_000:
            raise DiscoveredUniverseUnavailable("DISCOVERY_CATALOG_MISSING_OR_TOO_LARGE")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            needed = {"Symbol", "ISIN", "Series", "Company"}
            if not reader.fieldnames or not needed.issubset(reader.fieldnames):
                raise DiscoveredUniverseUnavailable("DISCOVERY_CATALOG_COLUMNS_INVALID")
            for row in reader:
                if None in row:
                    raise DiscoveredUniverseUnavailable("DISCOVERY_CATALOG_MALFORMED_ROW")
                sym = str(row.get("Symbol", "")).strip()
                isin = str(row.get("ISIN", "")).strip()
                if (not SYMBOL.fullmatch(sym) or not ISIN.fullmatch(isin) or
                        row.get("Series") != "EQ" or
                        not str(row.get("Company", "")).strip() or
                        sym in symbols or isin in isins):
                    raise DiscoveredUniverseUnavailable("DISCOVERY_CATALOG_AMBIGUOUS_SECURITY")
                symbols.add(sym)
                isins.add(isin)
                if len(symbols) > MAX_MAINBOARD:
                    raise DiscoveredUniverseUnavailable("DISCOVERY_CATALOG_TOO_MANY_SYMBOLS")
    except (OSError, csv.Error, UnicodeError):
        raise DiscoveredUniverseUnavailable("DISCOVERY_CATALOG_UNREADABLE") from None
    if len(symbols) != count:
        raise DiscoveredUniverseUnavailable("DISCOVERY_CATALOG_STATUS_COUNT_MISMATCH")
    return symbols
