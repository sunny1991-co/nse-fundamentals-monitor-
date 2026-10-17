"""Offline, human-review-gated import of ORIGINAL NSE Indices gross TRI CSVs.

Use *only* CSVs the owner exported from https://www.niftyindices.com/reports/
historical-data > Total returns Index Values > NIFTY 50. This cannot prove a
local file's origin: owner must check NSE index, dates and actual displayed
gross TRI values before explicit --install-reviewed.

This script NEVER requests blocked URLs, guesses index levels, fills dates,
changes fundamentals, sends broker orders or attests a file automatically.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re

from nifty50_tri_official_import import (
    atomic_bytes, OUTPUT_CSV, OUTPUT_META, SOURCE_PAGE, sha,
)
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

STAGE_CSV = "nifty50_tri_manual_candidate.csv"
STAGE_RECEIPT = "nifty50_tri_manual_receipt.json"
STATUS_FILE = "runtime/nifty50_tri_manual_import_status.json"
FORMAT = "NSE_INDICES_GROSS_NIFTY50_TRI_MANUAL_CSV_UNATTESTED"
MIN_INSTALL_DAYS = 1260
MAX_DAYS = 6000
MAX_SINGLE_FILE = 2_000_000
MAX_SOURCE_FILES = 40
FIRST_ALLOWED = date(2017, 1, 1)
DATES = ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y")
INDEX_ALIASES = {"indexname", "index", "index_name"}
VALUE_ALIASES = {"totalreturnsindex", "totalreturnindex"}
INDEX_NAME = "NIFTY 50"


def field_map(names):
    if not names:
        raise ValueError("OFFICIAL_TRI_CSV_HEADER_REQUIRED")
    values = {}
    for name in names:
        if not isinstance(name, str):
            raise ValueError("OFFICIAL_TRI_CSV_HEADER_INVALID")
        key = re.sub("[^a-z0-9]", "", name.lower().lstrip("\ufeff"))
        if not key or key in values:
            raise ValueError("OFFICIAL_TRI_CSV_AMBIGUOUS_HEADER")
        values[key] = name
    index = next((values[k] for k in ("indexname", "index") if k in values), None)
    numeric = next((values[k] for k in VALUE_ALIASES if k in values), None)
    day = values.get("date")
    if not index or not numeric or not day:
        raise ValueError("OFFICIAL_GROSS_TRI_COLUMNS_REQUIRED")
    return index, day, numeric


def read_official_export(blob: bytes, *, today: date):
    """Gross TRI only: rejects Close (price) and Net Total Return columns."""
    if not isinstance(blob, bytes) or not 0 < len(blob) <= MAX_SINGLE_FILE:
        raise ValueError("OFFICIAL_TRI_CSV_FILE_SIZE_INVALID")
    try:
        text = blob.decode("utf-8-sig")
        if text.lstrip().lower().startswith(("<html", "<!doctype")) or "\x00" in text:
            raise ValueError("OFFICIAL_TRI_CSV_NOT_CSV")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        index, day_field, gross = field_map(reader.fieldnames)
        rows = {}
        for item in reader:
            if None in item:
                raise ValueError("OFFICIAL_TRI_CSV_EXTRA_FIELDS")
            if len(rows) > MAX_DAYS:
                raise ValueError("OFFICIAL_TRI_CSV_TOO_MANY_ROWS")
            name = " ".join((item[index] or "").strip().upper().split())
            if name != INDEX_NAME:
                raise ValueError("OFFICIAL_TRI_CSV_WRONG_INDEX")
            raw_date = (item[day_field] or "").strip()
            d = None
            for pattern in DATES:
                try:
                    d = datetime.strptime(raw_date, pattern).date()
                    break
                except ValueError:
                    continue
            if d is None or d < FIRST_ALLOWED or d > today:
                raise ValueError("OFFICIAL_TRI_CSV_DATE_INVALID")
            try:
                level = Decimal(str(item[gross] or "").replace(",", "").strip())
            except (InvalidOperation, TypeError):
                raise ValueError("OFFICIAL_TRI_CSV_VALUE_INVALID") from None
            if not level.is_finite() or not 0 < level < 10**9:
                raise ValueError("OFFICIAL_TRI_CSV_VALUE_INVALID")
            if d in rows:
                raise ValueError("OFFICIAL_TRI_CSV_DUPLICATE_DAY")
            rows[d] = format(level, "f")
        if not rows:
            raise ValueError("OFFICIAL_TRI_CSV_NO_DATA")
        return rows
    except UnicodeError:
        raise ValueError("OFFICIAL_TRI_CSV_ENCODING_INVALID") from None


def csv_payload(values):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=("Date", "Close"), lineterminator="\n")
    writer.writeheader()
    writer.writerows(
        {"Date": day.isoformat(), "Close": values[day]}
        for day in sorted(values)
    )
    return buffer.getvalue().encode("utf-8")


def read_staged(root):
    csv_path, meta_path = root/STAGE_CSV, root/STAGE_RECEIPT
    if not csv_path.exists() and not meta_path.exists():
        return {}, []
    if not csv_path.is_file() or not meta_path.is_file():
        raise ValueError("STAGED_TRI_METADATA_OR_CSV_MISSING")
    try:
        payload = csv_path.read_bytes()
        receipt = json.loads(meta_path.read_text(encoding="utf-8"))
        if (receipt.get("format") != FORMAT
                or receipt.get("payload_sha256") != sha(payload)
                or receipt.get("operator_source_checked") is not False
                or not isinstance(receipt.get("raw_export_sha256s"), list)
                or not 1 <= len(receipt["raw_export_sha256s"]) <= MAX_SOURCE_FILES):
            raise ValueError()
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
        if reader.fieldnames != ["Date", "Close"]:
            raise ValueError()
        values = {}
        for item in reader:
            d = date.fromisoformat(item["Date"])
            n = Decimal(item["Close"])
            if d in values or d < FIRST_ALLOWED or not n.is_finite() or not 0<n<10**9:
                raise ValueError()
            values[d] = format(n, "f")
        if not values or len(values)>MAX_DAYS or csv_payload(values) != payload:
            raise ValueError()
        return values, list(receipt["raw_export_sha256s"])
    except (ValueError, KeyError, TypeError, UnicodeError, InvalidOperation, OSError):
        raise ValueError("STAGED_TRI_INTEGRITY_INVALID") from None


def stage_official_csv(root, source_path, *, now=None):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_REQUIRED")
    path = Path(source_path)
    if not path.is_file() or path.is_symlink():
        raise ValueError("EXPLICIT_LOCAL_CSV_PATH_REQUIRED")
    raw = path.read_bytes()
    if len(raw)>MAX_SINGLE_FILE:
        raise ValueError("OFFICIAL_TRI_CSV_FILE_SIZE_INVALID")
    today = now.date()
    incoming = read_official_export(raw, today=today)
    merged, digests = read_staged(root)
    digest = sha(raw)
    if digest in digests:
        raise ValueError("SOURCE_FILE_ALREADY_IMPORTED")
    if len(digests) >= MAX_SOURCE_FILES:
        raise ValueError("MAX_SOURCE_FILES_REACHED")
    for day, price in incoming.items():
        if day in merged and Decimal(merged[day]) != Decimal(price):
            raise ValueError("OVERLAPPING_SOURCE_TRI_VALUES_CONFLICT")
    merged.update(incoming)
    if len(merged)>MAX_DAYS:
        raise ValueError("TOO_MANY_TRI_DAYS")
    payload = csv_payload(merged)
    receipts = digests + [digest]
    receipt = {
        "format": FORMAT,
        "operator_source_checked": False,
        "downloaded_from_official_site_confirmed_by_owner": False,
        "source_page": SOURCE_PAGE,
        "raw_export_sha256s": receipts,
        "payload_sha256": sha(payload),
        "rows": len(merged),
        "first_date": min(merged).isoformat(),
        "last_date": max(merged).isoformat(),
        "last_staged_utc": now.isoformat(),
        "trading_orders": "DISABLED",
    }
    # Receipt last: interrupted write fails closed on next invocation.
    atomic_bytes(root/STAGE_CSV, payload)
    atomic_bytes(root/STAGE_RECEIPT,
                 (json.dumps(receipt, indent=2)+"\n").encode())
    result = {
        "state": "OFFICIAL_CSV_STAGED_UNREVIEWED",
        "source_files_staged": len(receipts),
        "staged_daily_rows": len(merged),
        "first_date": min(merged).isoformat(),
        "last_date": max(merged).isoformat(),
        "enough_rows_for_review": len(merged)>=MIN_INSTALL_DAYS,
        "operator_source_checked": False,
        "trading_orders": "DISABLED",
    }
    atomic_status(root/STATUS_FILE, result)
    return result


def install_reviewed(root, *, now=None):
    """A manual acknowledgement of actual official source review is essential."""
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_REQUIRED")
    values, digests = read_staged(root)
    if not values or not digests:
        raise ValueError("NO_MANUAL_OFFICIAL_CSV_CANDIDATE")
    if len(values) < MIN_INSTALL_DAYS:
        raise ValueError("INSUFFICIENT_1260_VERIFIED_INDEX_DAYS")
    if now.date() - max(values) > timedelta(days=45):
        raise ValueError("MANUAL_OFFICIAL_TRI_OUTDATED")
    dest, meta = root/OUTPUT_CSV, root/OUTPUT_META
    if dest.exists() or meta.exists():
        raise ValueError("BENCHMARK_ALREADY_INSTALLED_REFUSE_OVERWRITE")
    payload = csv_payload(values)
    receipt = json.loads((root/STAGE_RECEIPT).read_text(encoding="utf-8"))
    if receipt.get("payload_sha256") != sha(payload):
        raise ValueError("STAGED_TRI_INTEGRITY_INVALID")
    metadata = {
        "type": "OFFICIAL_NIFTY_50_TRI",
        "index_name": INDEX_NAME + " TRI",
        "official_source_url": SOURCE_PAGE,
        "source_method": FORMAT,
        "raw_source_files_sha256": digests,
        "sha256": sha(payload),
        "operator_source_checked": True,
        "source_review": "EXPLICIT_OWNER_CSV_EXPORT_AND_TRI_VALUES_REVIEW",
        "reviewed_at_utc": now.isoformat(),
        "source_is_independently_audited": False,
        "trading_orders": "DISABLED",
    }
    atomic_bytes(dest, payload)
    atomic_bytes(meta, (json.dumps(metadata,indent=2)+"\n").encode())
    result = {
        "state": "OWNER_REVIEWED_MANUAL_OFFICIAL_GROSS_TRI_INSTALLED_RESEARCH_ONLY",
        "rows": len(values),
        "source_files": len(digests),
        "operator_source_checked": True,
        "strategy_profitability_verified": False,
        "trading_orders": "DISABLED",
    }
    atomic_status(root/STATUS_FILE, result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    options=parser.add_mutually_exclusive_group(required=True)
    options.add_argument("--import-csv", metavar="OFFICIAL_NSE_CSV_FILE")
    options.add_argument("--install-reviewed", action="store_true")
    args=parser.parse_args()
    try:
        result=(stage_official_csv(data_root(),args.import_csv)
                if args.import_csv is not None else install_reviewed(data_root()))
    except (OSError,ValueError) as exc:
        known={
            "OFFICIAL_TRI_CSV_FILE_SIZE_INVALID",
            "OFFICIAL_TRI_CSV_NOT_CSV",
            "OFFICIAL_TRI_CSV_AMBIGUOUS_HEADER",
            "OFFICIAL_GROSS_TRI_COLUMNS_REQUIRED",
            "OFFICIAL_TRI_CSV_WRONG_INDEX",
            "OFFICIAL_TRI_CSV_DATE_INVALID",
            "OFFICIAL_TRI_CSV_VALUE_INVALID",
            "OFFICIAL_TRI_CSV_DUPLICATE_DAY",
            "OFFICIAL_TRI_CSV_NO_DATA",
            "OFFICIAL_TRI_CSV_EXTRA_FIELDS",
            "STAGED_TRI_INTEGRITY_INVALID",
            "STAGED_TRI_METADATA_OR_CSV_MISSING",
            "EXPLICIT_LOCAL_CSV_PATH_REQUIRED",
            "SOURCE_FILE_ALREADY_IMPORTED",
            "MAX_SOURCE_FILES_REACHED",
            "OVERLAPPING_SOURCE_TRI_VALUES_CONFLICT",
            "TOO_MANY_TRI_DAYS",
            "NO_MANUAL_OFFICIAL_CSV_CANDIDATE",
            "INSUFFICIENT_1260_VERIFIED_INDEX_DAYS",
            "MANUAL_OFFICIAL_TRI_OUTDATED",
            "BENCHMARK_ALREADY_INSTALLED_REFUSE_OVERWRITE",
        }
        result={
            "state":"OFFICIAL_TRI_CSV_IMPORT_BLOCKED",
            "reason_code":str(exc) if str(exc) in known else "OFFICIAL_CSV_INVALID_OR_UNREADABLE",
            "trading_orders":"DISABLED",
        }
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
