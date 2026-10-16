"""Optional evidence contracts for TRI benchmark and dated earnings events.

Source metadata is an operator attestation, NOT independent verification.
Missing or invalid inputs disable the dependent research hypothesis only.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
import hashlib
import io
import json
import math
from pathlib import Path
import re
from urllib.parse import urlparse

import pandas as pd


def _file_and_meta(root, basename, expected_type):
    root = Path(root)
    path = root / (basename + ".csv")
    meta_path = root / (basename + "_metadata.json")
    if not path.is_file() or not meta_path.is_file():
        raise ValueError("SOURCE_AND_METADATA_REQUIRED")
    payload = path.read_bytes()
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    if (metadata.get("type") != expected_type
            or metadata.get("sha256") != hashlib.sha256(payload).hexdigest()
            or metadata.get("operator_source_checked") is not True):
        raise ValueError("UNVERIFIED_SOURCE_METADATA")
    return payload, metadata


def _official(url, valid_hosts):
    value = urlparse(str(url))
    return (value.scheme == "https" and value.hostname in valid_hosts
            and not value.username and not value.password)


def read_nifty50_tri(root):
    """Read attested NIFTY 50 TRI price levels, never regular NIFTY price index."""
    payload, metadata = _file_and_meta(
        root, "nifty50_tri_history", "OFFICIAL_NIFTY_50_TRI"
    )
    if (metadata.get("index_name") != "NIFTY 50 TRI"
            or not _official(metadata.get("official_source_url"), {
                "niftyindices.com", "www.niftyindices.com",
                "nseindia.com", "www.nseindia.com",
            })):
        raise ValueError("NIFTY_50_TRI_OFFICIAL_SOURCE_REQUIRED")
    frame = pd.read_csv(io.BytesIO(payload), dtype={"Date": str})
    if not {"Date", "Close"}.issubset(frame.columns) or frame.empty:
        raise ValueError("BENCHMARK_OHLCV_REQUIRED")
    frame = frame[["Date", "Close"]]
    if not frame.Date.str.fullmatch(r"\d{4}-\d{2}-\d{2}").all():
        raise ValueError("BENCHMARK_DAILY_DATES_REQUIRED")
    dates = pd.to_datetime(frame.Date, format="%Y-%m-%d", errors="raise")
    levels = pd.to_numeric(frame.Close, errors="raise")
    if dates.duplicated().any() or not all(
        math.isfinite(float(v)) and float(v) > 0 for v in levels
    ):
        raise ValueError("BAD_OR_DUPLICATE_BENCHMARK")
    return pd.Series(levels.to_numpy(dtype=float), index=pd.DatetimeIndex(dates)).sort_index(), metadata["sha256"]


def read_official_earnings(root):
    """Accept only attested consolidated and dated original-source filings.

    The first eligible signal occurs *after* the announcement's IST calendar
    date, never on announcement day. No historical current fundamentals are
    substituted into events.
    """
    payload, _metadata = _file_and_meta(
        root, "official_earnings_events", "OFFICIAL_POINT_IN_TIME_NSE_EARNINGS"
    )
    required = {
        "Symbol", "Announced_At_IST", "Revenue_YoY_pct",
        "PAT_YoY_pct", "Official_URL", "Document_SHA256",
        "Basis", "Verified_Primary_Source",
    }
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    if not rows or not required.issubset(rows[0]):
        raise ValueError("EARNINGS_EVENT_FIELDS_REQUIRED")
    events = {}
    for row in rows:
        symbol = str(row["Symbol"]).strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9&_.-]*", symbol):
            raise ValueError("INVALID_EARNINGS_SYMBOL")
        if (row["Basis"].upper() != "CONSOLIDATED"
                or row["Verified_Primary_Source"].strip().lower() != "true"
                or not _official(row["Official_URL"], {
                    "nseindia.com", "www.nseindia.com", "nsearchives.nseindia.com",
                })
                or not re.fullmatch("[0-9a-fA-F]{64}", row["Document_SHA256"])):
            raise ValueError("PRIMARY_SOURCE_AND_BASIS_UNVERIFIED")
        stamp = datetime.fromisoformat(row["Announced_At_IST"])
        if (stamp.tzinfo is None or stamp.utcoffset() != timedelta(hours=5, minutes=30)):
            raise ValueError("EXACT_IST_RELEASE_TIME_REQUIRED")
        rev, pat = float(row["Revenue_YoY_pct"]), float(row["PAT_YoY_pct"])
        if not math.isfinite(rev) or not math.isfinite(pat):
            raise ValueError("NONFINITE_EARNINGS_GROWTH")
        # Fixed research hypothesis: positive YoY operating and bottom-line
        # earnings surprise; both ratios must come from dated source evidence.
        if rev >= 10 and pat >= 15:
            events.setdefault(symbol, []).append(pd.Timestamp(stamp.date()))
    return events, hashlib.sha256(payload).hexdigest()
