#!/usr/bin/env python3
"""Cache official NSE equity industry classification for screened symbols."""
from __future__ import annotations

from research_monitor.paths import data_root
import argparse
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
import requests

ROOT = data_root()
LANDING_URL = "https://www.nseindia.com/"
QUOTE_PAGE_URL = "https://www.nseindia.com/get-quotes/equity"
QUOTE_URL = "https://www.nseindia.com/api/quote-equity"
MAP_FILE = ROOT / "nse_sector_map.csv"
MAX_LOOKUPS_PER_RUN = 3500
MAX_CONSECUTIVE_BLOCKS = 2  # Stop on persistent NSE 403/429; do not waste hours retrying.
REFRESH_DAYS = 60
RETRY_FAILED_AFTER_DAYS = 1  # Recheck NSE access daily; never treat 403 as successful classification.


def read_csv(path):
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
    return df


def session_create():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": LANDING_URL,
    })
    r = s.get(LANDING_URL, timeout=30)
    r.raise_for_status()
    return s


def candidate_symbols():
    files = [
        ROOT / "FY2425.csv",
        ROOT / "NSE_FY2025-26_Consolidated_Fundamentals.csv",
        ROOT / "annual_historical_results.csv",
    ]
    symbols = set()
    for path in files:
        df = read_csv(path)
        if "Symbol" in df:
            symbols.update(x.strip().upper() for x in df["Symbol"].astype(str) if x.strip() and x.strip().lower() != "nan")
    return sorted(symbols)


def fresh_date(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def lookup(s, sym):
    last = None
    quote_page = f"{QUOTE_PAGE_URL}?symbol={sym}"
    for attempt in range(3):
        try:
            r = s.get(
                QUOTE_URL, params={"symbol": sym}, timeout=20,
                headers={"Referer": quote_page, "Accept": "application/json,text/plain,*/*"},
            )
            if r.status_code in (403, 429) and attempt == 0:
                # One normal-browser warm-up retry is allowed. This is not a
                # proxy/identity bypass; if NSE still refuses access we stop.
                page = s.get(
                    quote_page, timeout=20,
                    headers={"Referer": LANDING_URL, "Accept": "text/html,application/xhtml+xml,*/*"},
                )
                if page.status_code == 200:
                    r = s.get(
                        QUOTE_URL, params={"symbol": sym}, timeout=20,
                        headers={"Referer": quote_page, "Accept": "application/json,text/plain,*/*"},
                    )
            if r.status_code in (403, 429):
                raise requests.HTTPError(f"NSE quote HTTP {r.status_code} (blocked)")
            if r.status_code in (500, 502, 503, 504):
                raise requests.HTTPError(f"NSE quote HTTP {r.status_code}")
            r.raise_for_status()
            data = r.json()
            info = data.get("industryInfo") or {}
            if not any(info.get(k) for k in ("macro", "sector", "industry", "basicIndustry")):
                raise ValueError("industryInfo missing")
            return {
                "Symbol": sym,
                "Macro_Economic_Sector": info.get("macro", ""),
                "Sector": info.get("sector", ""),
                "Industry": info.get("industry", ""),
                "Basic_Industry": info.get("basicIndustry", ""),
                "Lookup_Status": "OK",
                "Lookup_Date": date.today().isoformat(),
                "Source_URL": f"{QUOTE_URL}?symbol={sym}",
                "Error": "",
            }
        except Exception as exc:
            last = exc
            if "HTTP 403" in str(exc) or "HTTP 429" in str(exc):
                break  # Blocked requests should not be retried for every symbol.
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    return {
        "Symbol": sym, "Macro_Economic_Sector": "", "Sector": "",
        "Industry": "", "Basic_Industry": "", "Lookup_Status": "ERROR",
        "Lookup_Date": date.today().isoformat(),
        "Source_URL": f"{QUOTE_URL}?symbol={sym}", "Error": str(last),
    }


def main(limit: int = MAX_LOOKUPS_PER_RUN):
    if not 1 <= int(limit) <= MAX_LOOKUPS_PER_RUN:
        raise ValueError(f"limit must be 1..{MAX_LOOKUPS_PER_RUN}")
    limit = int(limit)
    current = read_csv(MAP_FILE)
    symbols = candidate_symbols()
    cache = {}
    if not current.empty and "Symbol" in current:
        for _, row in current.iterrows():
            cache[str(row.get("Symbol", "")).upper()] = row.to_dict()
    to_lookup = []
    today = date.today()
    for sym in symbols:
        old = cache.get(sym)
        dt = fresh_date(old.get("Lookup_Date")) if old else None
        status = str(old.get("Lookup_Status", "")) if old else ""
        max_age = REFRESH_DAYS if status == "OK" else RETRY_FAILED_AFTER_DAYS
        if old is None or dt is None or (today - dt).days >= max_age:
            to_lookup.append(sym)
    to_lookup = to_lookup[:limit]
    print(f"Symbols needed: {len(symbols)}; cached: {len(cache)}; lookups this run: {len(to_lookup)}")
    s = None
    if to_lookup:
        try:
            s = session_create()
        except requests.RequestException as exc:
            print(f"NSE session unavailable: {exc}; preserving old sector cache and leaving unvisited symbols pending.")
            # Do not date-stamp all 1,700+ symbols after a *single* request failed.
            # That would postpone the entire universe's retry unnecessarily.
            for sym in to_lookup[:MAX_CONSECUTIVE_BLOCKS]:
                if str(cache.get(sym, {}).get("Lookup_Status", "")) != "OK":
                    cache[sym] = {"Symbol": sym, "Lookup_Status": "ERROR",
                                  "Lookup_Date": today.isoformat(), "Error": str(exc),
                                  "Source_URL": LANDING_URL}
            to_lookup = []
    consecutive_blocks = 0
    for i, sym in enumerate(to_lookup, 1):
        cache[sym] = lookup(s, sym)
        blocked = "HTTP 403" in cache[sym]["Error"] or "HTTP 429" in cache[sym]["Error"]
        consecutive_blocks = consecutive_blocks + 1 if blocked else 0
        if consecutive_blocks >= MAX_CONSECUTIVE_BLOCKS:
            print("NSE sector API blocked; leaving remaining symbols unclassified, never guessing sector.")
            break
        if i % 100 == 0:
            print(f"Classified {i}/{len(to_lookup)} symbols")
        time.sleep(0.06)
    columns = ["Symbol", "Macro_Economic_Sector", "Sector", "Industry", "Basic_Industry", "Lookup_Status", "Lookup_Date", "Source_URL", "Error"]
    out = pd.DataFrame([cache[k] for k in sorted(cache)])
    for col in columns:
        if col not in out:
            out[col] = ""
    out = out.fillna("")
    out = out[columns].sort_values("Symbol")
    out.to_csv(MAP_FILE, index=False, na_rep="")
    success = int(out["Lookup_Status"].eq("OK").sum())
    errors = int(out["Lookup_Status"].ne("OK").sum())
    print(f"Sector map saved. Official sector rows={success}; missing/error rows={errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=MAX_LOOKUPS_PER_RUN,
                        help="Maximum stale/missing symbols to query this run")
    args = parser.parse_args()
    main(args.limit)


