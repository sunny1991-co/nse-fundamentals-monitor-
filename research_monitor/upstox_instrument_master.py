"""Bounded, unauthenticated Upstox NSE BOD instrument-to-ISIN reference map.

This is a CURRENT vendor symbol crosswalk, not a historic NSE mainboard
constituent list, audited statement, share-price feed, or trading instrument
authorization. Strict NSE XBRL/sector/annual screens remain untouched.
Only current NSE_EQ + EQ entries matched to *both* local NSE annual baseline
symbol lists are retained; conflicts are dropped, never silently resolved.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re

import requests

from research_monitor.schedule import atomic_status

MASTER_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
)
MAP_NAME = "upstox_nse_eq_isin_map.csv"
METADATA_NAME = "upstox_nse_eq_isin_map_metadata.json"
MAX_COMPRESSED_BYTES = 30_000_000
MAX_EXPANDED_BYTES = 70_000_000
MIN_CROSSWALK_SYMBOLS = 5
ISIN = re.compile(r"^IN[A-Z0-9]{10}$")
SYMBOL = re.compile(r"^[A-Z0-9&._-]{1,40}$")


class InstrumentMapUnavailable(RuntimeError):
    pass


def _safe_baseline_symbols(root: Path):
    """Require both annual source lists; their common symbols are not
    individually endorsed by the vendor map for live orders."""
    filenames = (
        "FY2425.csv",
        "NSE_FY2025-26_Consolidated_Fundamentals.csv",
    )
    sets = []
    for filename in filenames:
        path = root / filename
        if not path.is_file():
            raise InstrumentMapUnavailable("BOTH_ANNUAL_BASELINE_FILES_REQUIRED")
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames or "Symbol" not in reader.fieldnames:
                    raise InstrumentMapUnavailable("ANNUAL_BASELINE_SYMBOL_FIELD_MISSING")
                symbols = {
                    row["Symbol"].strip().upper()
                    for row in reader if isinstance(row.get("Symbol"), str)
                    and SYMBOL.fullmatch(row["Symbol"].strip().upper())
                }
        except (OSError,UnicodeDecodeError,csv.Error):
            raise InstrumentMapUnavailable("ANNUAL_BASELINE_UNREADABLE") from None
        if not symbols:
            raise InstrumentMapUnavailable("ANNUAL_BASELINE_EMPTY")
        sets.append(symbols)
    eligible = sets[0] & sets[1]
    if not eligible:
        raise InstrumentMapUnavailable("NO_SHARED_ANNUAL_BASELINE_SYMBOLS")
    return eligible


def _download_master(session):
    """Single public GET; enforce strict compressed and expanded limits.

    No broker bearer token is attached to the public BOD download.
    """
    try:
        with session.get(
            MASTER_URL,
            timeout=(12, 65),
            allow_redirects=False,
            stream=True,
            headers={"Accept": "application/gzip,application/octet-stream"},
        ) as response:
            if response.status_code != 200:
                raise InstrumentMapUnavailable(
                    "UPSTOX_INSTRUMENT_MASTER_HTTP_"+str(response.status_code)
                )
            raw = bytearray()
            for part in response.iter_content(chunk_size=65536):
                if not isinstance(part,bytes):
                    raise InstrumentMapUnavailable("MASTER_RESPONSE_NOT_BYTES")
                raw.extend(part)
                if len(raw)>MAX_COMPRESSED_BYTES:
                    raise InstrumentMapUnavailable("MASTER_COMPRESSED_SIZE_LIMIT")
    except InstrumentMapUnavailable:
        raise
    except requests.RequestException:
        raise InstrumentMapUnavailable("MASTER_NETWORK_ERROR") from None

    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as reader:
            payload = reader.read(MAX_EXPANDED_BYTES+1)
        if len(payload)>MAX_EXPANDED_BYTES:
            raise InstrumentMapUnavailable("MASTER_UNCOMPRESSED_SIZE_LIMIT")
        decoded = json.loads(payload)
    except InstrumentMapUnavailable:
        raise
    except (OSError,EOFError,ValueError,TypeError,UnicodeError):
        raise InstrumentMapUnavailable("MASTER_GZIP_OR_JSON_INVALID") from None
    if not isinstance(decoded,list):
        raise InstrumentMapUnavailable("MASTER_EXPECTED_JSON_LIST")
    return decoded, hashlib.sha256(raw).hexdigest()


def _map_symbols(master_rows, eligible):
    """Exclude derivatives, BSE, non-EQ (including SME series), bad ISINs.

    Exclude duplicates/conflicts both by symbol and ISIN, even if a later
    row tries to reintroduce a previously conflicting mapping.
    """
    candidates = {}
    conflicted_symbols = set()
    conflicted_isins = set()
    seen_by_isin = {}

    count_eq = 0
    for row in master_rows:
        if not isinstance(row,dict):
            continue
        if not (
            row.get("exchange")=="NSE"
            and row.get("segment")=="NSE_EQ"
            and row.get("instrument_type")=="EQ"
        ):
            continue
        count_eq+=1
        symbol = row.get("trading_symbol")
        isin = row.get("isin")
        if not isinstance(symbol,str) or not isinstance(isin,str):
            continue
        symbol,isin=symbol.strip().upper(),isin.strip().upper()
        if (
            not SYMBOL.fullmatch(symbol)
            or not ISIN.fullmatch(isin)
            or row.get("instrument_key")!="NSE_EQ|"+isin
            or row.get("lot_size") != 1
            or symbol not in eligible
        ):
            continue
        if symbol in candidates and candidates[symbol] != isin:
            conflicted_symbols.add(symbol)
        if isin in seen_by_isin and seen_by_isin[isin] != symbol:
            conflicted_isins.add(isin)
        if symbol not in candidates:
            candidates[symbol]=isin
        if isin not in seen_by_isin:
            seen_by_isin[isin]=symbol

    chosen={
        symbol:isin for symbol,isin in candidates.items()
        if symbol not in conflicted_symbols and isin not in conflicted_isins
    }
    if len(chosen)<MIN_CROSSWALK_SYMBOLS:
        raise InstrumentMapUnavailable("TOO_FEW_UNAMBIGUOUS_NSE_EQ_SYMBOLS")
    return chosen, {
        "nse_segment_eq_rows":count_eq,
        "verified_by_vendor_crosswalk":len(chosen),
        "unmatched_baseline_intersection":len(eligible-chosen.keys()),
        "conflicting_symbols":len(conflicted_symbols),
        "conflicting_isins":len(conflicted_isins),
        "overlapping_baseline_symbols":len(eligible),
    }


def sync(root, session=None):
    """Update only a PRIVATE vendor crosswalk, never original NSE CSV files.

    No implicit network retry and no caching of suspicious/unverified
    downloaded contents. A failed sync does not silently reuse a stale map.
    """
    root=Path(root)
    eligible=_safe_baseline_symbols(root)
    owned=session is None
    session=session or requests.Session()
    try:
        raw,digest=_download_master(session)
    finally:
        if owned:
            session.close()
    chosen,summary=_map_symbols(raw,eligible)
    folder=root/"runtime"/"experiments"/"upstox-nse-historical-reference-v1"
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    map_path=folder/MAP_NAME
    temp=map_path.with_suffix(".csv.tmp")
    with temp.open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(
            stream, fieldnames=["SYMBOL","ISIN","SERIES","SOURCE"]
        )
        writer.writeheader()
        for symbol in sorted(chosen):
            writer.writerow({
                "SYMBOL":symbol,
                "ISIN":chosen[symbol],
                "SERIES":"EQ",
                "SOURCE":"UPSTOX_NSE_BOD_CURRENT_VENDOR_MAP",
            })
    os.chmod(temp,0o600)
    os.replace(temp,map_path)
    metadata={
        "scope":"CURRENT_NSE_EQ_VENDOR_CROSSWALK_ONLY_NOT_MAINBOARD_ATTESTATION",
        "source_url":MASTER_URL,
        "source_sha256":digest,
        "fetched_at_utc":datetime.now(timezone.utc).isoformat(),
        "mapping_path":str(map_path),
        "data_source":"UPSTOX_PUBLIC_NSE_BOD_JSON_GZIP",
        **summary,
        "verified_mainboard_history":False,
        "nse_xbrl_verified":False,
        "nse_sector_verified":False,
        "automatic_trading":False,
    }
    atomic_status(folder/METADATA_NAME,metadata)
    return metadata
