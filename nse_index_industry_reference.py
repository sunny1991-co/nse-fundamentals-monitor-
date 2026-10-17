"""Official NSE Indices broad-index industry reference, NEVER trade eligibility.

The constituent CSV supplies a coarse 'Industry' label for Nifty Total Market
stocks. It does NOT contain NSE Indices' full 4-tier issuer classification;
do not merge it into nse_sector_map.csv or claim an audited sector pass.
No API credentials, price feeds, proxy workarounds, or broker orders.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import requests

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

SOURCE_URL = (
    "https://nsearchives.nseindia.com/content/indices/"
    "ind_niftytotalmarket_list.csv"
)
REFERENCE_NAME = "nse_totalmarket_industry_reference.csv"
STATUS_NAME = "nse_totalmarket_industry_reference_status.json"
MAX_CSV_BYTES = 1_500_000
MAX_ROWS = 1200
SYMBOL = re.compile(r"[A-Z0-9&._-]{1,40}\Z")
FIELDS = (
    "Symbol", "Company_Name", "Index_Industry", "Source_URL",
    "Source_SHA256", "Fetched_At_UTC", "Trust_Scope",
)
TRUST_SCOPE = "OFFICIAL_NSE_INDICES_COARSE_REFERENCE_ONLY_NOT_4_TIER"


def _text(value, max_length):
    if not isinstance(value, str):
        raise ValueError("INVALID_OFFICIAL_CSV_TEXT")
    value = value.strip()
    if not value or len(value) > max_length or any(
        ord(ch) < 32 or ord(ch) == 127 for ch in value
    ):
        raise ValueError("INVALID_OFFICIAL_CSV_TEXT")
    return value


def parse_constituents(blob, *, stamp):
    """Reject malformed/inconsistent source instead of inventing a mapping."""
    if not isinstance(blob, bytes) or not 0 < len(blob) <= MAX_CSV_BYTES:
        raise ValueError("INVALID_SOURCE_SIZE")
    if b"\x00" in blob or blob.lstrip()[:5].lower() == b"<html":
        raise ValueError("NOT_A_CONSTITUENT_CSV")
    try:
        raw = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("OFFICIAL_CSV_ENCODING_INVALID") from None
    reader = csv.DictReader(io.StringIO(raw, newline=""))
    if not reader.fieldnames:
        raise ValueError("OFFICIAL_CSV_HEADER_MISSING")
    names = {h.strip().lower(): h for h in reader.fieldnames if h}
    if not {"symbol", "company name", "industry"}.issubset(names):
        raise ValueError("OFFICIAL_CSV_COLUMNS_MISSING")
    digest = hashlib.sha256(blob).hexdigest()
    output = {}
    for row in reader:
        if len(output) >= MAX_ROWS:
            raise ValueError("INDEX_REFERENCE_ROWS_EXCEED_LIMIT")
        if None in row:
            raise ValueError("OFFICIAL_CSV_EXTRA_COLUMNS")
        symbol = _text(row[names["symbol"]], 40).upper()
        if not SYMBOL.fullmatch(symbol):
            raise ValueError("INVALID_OFFICIAL_SYMBOL")
        company = _text(row[names["company name"]], 200)
        industry = _text(row[names["industry"]], 120)
        if symbol in output:
            raise ValueError("DUPLICATE_OFFICIAL_INDEX_SYMBOL")
        output[symbol] = {
            "Symbol": symbol,
            "Company_Name": company,
            "Index_Industry": industry,
            "Source_URL": SOURCE_URL,
            "Source_SHA256": digest,
            "Fetched_At_UTC": stamp,
            "Trust_Scope": TRUST_SCOPE,
        }
    if not output:
        raise ValueError("EMPTY_CONSTITUENT_REFERENCE")
    return [output[k] for k in sorted(output)]


def _publish_csv(path, rows):
    """Atomic publish; failure must not destroy last known good reference."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", delete=False,
            dir=path.parent, prefix=".official_index_",
        ) as output:
            temp_name = output.name
            writer = csv.DictWriter(output, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def collect(root, now=None, *, fetch=None):
    """Bounded one-request read-only fetch; 403/429 preserve prior data."""
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    stamp = now.astimezone(timezone.utc).isoformat()
    status = {
        "updated_at_utc": stamp,
        "state": "SOURCE_UNAVAILABLE",
        "source_url": SOURCE_URL,
        "classification_scope": TRUST_SCOPE,
        "strict_fundamental_pass_changes": 0,
        "nse_sector_map_modified": False,
        "live_orders": "DISABLED",
        "reference_rows_this_run": 0,
    }
    getter = fetch or requests.get
    try:
        response = getter(
            SOURCE_URL, timeout=35, allow_redirects=False,
            headers={"User-Agent": "NSE research archive integrity checker"},
        )
        if response.status_code in (403, 429):
            status["state"] = f"BLOCKED_HTTP_{response.status_code}"
        elif response.status_code != 200 or response.url != SOURCE_URL:
            status["state"] = "SOURCE_HTTP_OR_REDIRECT_UNVERIFIED"
        else:
            entries = parse_constituents(response.content, stamp=stamp)
            _publish_csv(root/REFERENCE_NAME, entries)
            status["state"] = "OFFICIAL_COARSE_REFERENCE_UPDATED"
            status["reference_rows_this_run"] = len(entries)
            status["sha256"] = entries[0]["Source_SHA256"]
    except (requests.RequestException, OSError, ValueError, csv.Error) as exc:
        status["state"] = "SOURCE_OR_SCHEMA_INVALID"
        status["error_type"] = type(exc).__name__
    atomic_status(root/"runtime"/STATUS_NAME, status)
    return status


def main():
    result = collect(data_root())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
