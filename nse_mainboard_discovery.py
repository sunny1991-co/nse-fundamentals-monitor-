#!/usr/bin/env python3
"""Daily NSE mainboard (EQ) discovery; watch-only, no trading authorization.

Uses the official NSE Equity and SME securities lists together. Neither an
Upstox NSE_EQ instrument nor a bhavcopy EQ row proves mainboard membership.
Discovery NEVER changes baseline annual data, history, strict shortlist,
swing_watchlist.json, orders, paper approvals, or SMS.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re

import requests

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
SME_URL = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
LANDING_URL = "https://www.nseindia.com/static/market-data/securities-available-for-trading"
CATALOG = "nse_mainboard_discovery_catalog.csv"
QUEUE = "nse_mainboard_discovery_queue.csv"
STATUS = "runtime/nse_mainboard_discovery_status.json"
MAX_BYTES = 2_000_000
MIN_MAINBOARD = 1_000
MIN_SME = 50
MAX_MAINBOARD = 5_000
MAX_SME = 3_000
SYMBOL = re.compile(r"^[A-Z0-9&._-]{1,40}$")
ISIN = re.compile(r"^IN[A-Z0-9]{10}$")
CATALOG_COLS = [
    "Symbol", "Company", "ISIN", "Listing_Date", "Series",
    "Baseline_Tracked", "Strict_Annual_Pass", "Strict_Final_Shortlist",
    "First_Seen_UTC", "New_Since_Prior_List", "Discovery_State",
]
QUEUE_COLS = CATALOG_COLS + ["Next_Evidence_Needed"]


class DiscoveryUnavailable(RuntimeError):
    pass


def normalize_header(value: str) -> str:
    return " ".join(str(value).strip().lstrip("\ufeff").upper().split())


def pick_column(headers: dict[str, str], *candidates: str) -> str | None:
    return next((headers[name] for name in candidates if name in headers), None)


def listing_rows(payload: bytes, *, minimum: int, maximum: int, is_sme: bool) -> dict[str, dict]:
    """Strict mainboard IDs; SME may provide symbol-only *exclusion* evidence.

    SME rows can ONLY remove securities from the mainboard list, never
    admit a symbol. A blank SME ISIN is not used for cross-ISIN exclusion.
    """
    label = "SME" if is_sme else "EQUITY"
    if not isinstance(payload, bytes) or not 64 <= len(payload) <= MAX_BYTES:
        raise DiscoveryUnavailable(label + "_OFFICIAL_CSV_INVALID_SIZE")
    try:
        decoded = payload.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(decoded, newline=""))
        headers = {normalize_header(h): h for h in (reader.fieldnames or []) if h}
        symbol_col = pick_column(headers, "SYMBOL", "TRADING SYMBOL", "TRADING_SYMBOL")
        isin_col = pick_column(headers, "ISIN NUMBER", "ISIN", "ISIN NO", "ISIN NO.", "ISIN CODE")
        name_col = pick_column(headers, "NAME OF COMPANY", "COMPANY NAME", "NAME", "SECURITY NAME")
        series_col = pick_column(headers, "SERIES", "SECURITY SERIES")
        date_col = pick_column(headers, "DATE OF LISTING", "LISTING DATE")
        if (not symbol_col or (not is_sme and
                (not isin_col or not name_col or not series_col))):
            # Only expose public CSV field names, never source row data or tokens.
            safe = [re.sub(r"[^A-Z0-9_ ]", "", h)[:28]
                    for h in list(headers)[:9]]
            raise DiscoveryUnavailable(
                label + "_OFFICIAL_CSV_HEADERS_UNEXPECTED:" + ",".join(safe)
            )
        by_symbol: dict[str, dict] = {}
        seen_isins: set[str] = set()
        for row in reader:
            if None in row:  # Extra delimiter or malformed CSV row.
                raise DiscoveryUnavailable(label + "_OFFICIAL_CSV_ROW_MALFORMED")
            sym = str(row.get(symbol_col, "")).strip().upper()
            isin = str(row.get(isin_col, "")).strip().upper() if isin_col else ""
            name = str(row.get(name_col, "")).strip() if name_col else ""
            series = str(row.get(series_col, "")).strip().upper() if series_col else ""
            if not sym and not isin and not name:
                continue
            if not SYMBOL.fullmatch(sym):
                raise DiscoveryUnavailable(label + "_OFFICIAL_CSV_INVALID_SYMBOL")
            if not is_sme:
                # On the *mainboard* list never infer identity from symbol alone.
                if not ISIN.fullmatch(isin) or not name:
                    raise DiscoveryUnavailable("EQUITY_OFFICIAL_CSV_INCOMPLETE_SECURITY")
                if series != "EQ":
                    continue
            elif isin and not ISIN.fullmatch(isin):
                raise DiscoveryUnavailable("SME_OFFICIAL_CSV_INVALID_OPTIONAL_ISIN")
            if sym in by_symbol or (isin and isin in seen_isins):
                raise DiscoveryUnavailable(label + "_OFFICIAL_CSV_CONFLICTING_INSTRUMENT")
            if isin:
                seen_isins.add(isin)
            by_symbol[sym] = {
                "Symbol": sym, "Company": name, "ISIN": isin,
                "Listing_Date": str(row.get(date_col, "")).strip() if date_col else "",
                "Series": series if is_sme else "EQ",
            }
        if not minimum <= len(by_symbol) <= maximum:
            raise DiscoveryUnavailable(label + "_OFFICIAL_CSV_IMPLAUSIBLE_UNIVERSE_SIZE")
        return by_symbol
    except (UnicodeError, csv.Error, TypeError, AttributeError):
        raise DiscoveryUnavailable(label + "_OFFICIAL_CSV_PARSE_ERROR") from None

def download_official(url: str, session: requests.Session) -> bytes:
    response = session.get(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*",
                 "Referer": LANDING_URL},
        timeout=30, allow_redirects=False,
    )
    if response.status_code != 200:
        raise DiscoveryUnavailable("NSE_OFFICIAL_HTTP_" + str(response.status_code))
    if len(response.content) > MAX_BYTES:
        raise DiscoveryUnavailable("NSE_OFFICIAL_RESPONSE_TOO_LARGE")
    return response.content


def load_symbols(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            col = next((x for x in (reader.fieldnames or [])
                        if normalize_header(x) == "SYMBOL"), None)
            if col is None:
                return set()
            return {sym for row in reader
                    if (sym := str(row.get(col, "")).strip().upper())
                    and SYMBOL.fullmatch(sym)}
    except (OSError, UnicodeError, csv.Error):
        raise DiscoveryUnavailable("LOCAL_BASELINE_CSV_UNREADABLE") from None


def selected_symbols(path: Path, flag: str) -> set[str]:
    if not path.is_file():
        return set()
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = {normalize_header(h): h for h in (reader.fieldnames or [])}
            if "SYMBOL" not in columns or normalize_header(flag) not in columns:
                return set()
            return {sym for row in reader
                    if str(row.get(columns[normalize_header(flag)], "")).strip().lower()
                    in ("true", "yes", "1")
                    if (sym := str(row.get(columns["SYMBOL"], "")).strip().upper())
                    and SYMBOL.fullmatch(sym)}
    except (OSError, UnicodeError, csv.Error):
        raise DiscoveryUnavailable("LOCAL_SCREEN_CSV_UNREADABLE") from None


def prior_first_seen(path: Path) -> dict[str, tuple[str, str]]:
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            if not {"Symbol", "ISIN", "First_Seen_UTC"}.issubset(rows.fieldnames or []):
                raise DiscoveryUnavailable("PRIOR_DISCOVERY_CATALOG_INVALID")
            result = {}
            for row in rows:
                symbol, isin = row.get("Symbol", ""), row.get("ISIN", "")
                if symbol in result or not SYMBOL.fullmatch(symbol) or not ISIN.fullmatch(isin):
                    raise DiscoveryUnavailable("PRIOR_DISCOVERY_CATALOG_INVALID")
                result[symbol] = (isin, row.get("First_Seen_UTC", ""))
            return result
    except (OSError, UnicodeError, csv.Error):
        raise DiscoveryUnavailable("PRIOR_DISCOVERY_CATALOG_UNREADABLE") from None


def write_csv_atomic(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def refresh(root: Path, fetch=None, *, min_main: int = MIN_MAINBOARD,
            min_sme: int = MIN_SME, min_baseline: int = 500,
            now: datetime | None = None) -> dict:
    """Only independently verified fresh NSE lists may replace the discovery report."""
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timezone-aware now required")
    stamp = now.astimezone(timezone.utc).isoformat()
    if fetch is None:
        with requests.Session() as session:
            main_raw = download_official(EQUITY_URL, session)
            sme_raw = download_official(SME_URL, session)
    else:
        main_raw, sme_raw = fetch(EQUITY_URL), fetch(SME_URL)
    main = listing_rows(main_raw, minimum=min_main, maximum=MAX_MAINBOARD, is_sme=False)
    sme = listing_rows(sme_raw, minimum=min_sme, maximum=MAX_SME, is_sme=True)
    sme_symbols = set(sme)
    sme_isins = {r["ISIN"] for r in sme.values() if r["ISIN"]}
    confirmed = {sym: row for sym, row in main.items()
                 if sym not in sme_symbols and row["ISIN"] not in sme_isins}
    if len(confirmed) < min_main:
        raise DiscoveryUnavailable("SME_EXCLUSIONS_LEFT_TOO_FEW_MAINBOARD_STOCKS")
    previous = prior_first_seen(root / CATALOG)
    if previous and not .85 <= len(confirmed) / len(previous) <= 1.20:
        raise DiscoveryUnavailable("MAINBOARD_UNIVERSE_SUDDEN_CHANGE_REVIEW_REQUIRED")

    baseline = (
        load_symbols(root / "FY2425.csv")
        | load_symbols(root / "NSE_FY2025-26_Consolidated_Fundamentals.csv")
        | load_symbols(root / "annual_fundamental_quality_report.csv")
    )
    if len(baseline) < min_baseline:
        raise DiscoveryUnavailable("ANNUAL_BASELINE_MISSING_OR_TOO_SMALL")
    strict = selected_symbols(
        root / "annual_fundamental_quality_report.csv", "Full_Fundamental_Criteria_Pass"
    )
    final = load_symbols(root / "final_fundamental_shortlist.csv")
    catalog, queue = [], []
    newly = 0
    for sym, row in sorted(confirmed.items()):
        original = previous.get(sym)
        unchanged_isin = original is not None and original[0] == row["ISIN"]
        first_seen = original[1] if unchanged_isin and original[1] else stamp
        new = not unchanged_isin
        newly += int(new)
        tracked = sym in baseline
        annual_pass = sym in strict
        final_pass = sym in final and annual_pass
        if not tracked:
            state = "NEW_OR_UNTRACKED_RESEARCH_ONLY"
            next_step = "VERIFIED_ANNUAL_FINANCIALS_AND_ADJUSTED_PRICE_HISTORY_REQUIRED"
        elif not annual_pass:
            state = "TRACKED_STRICT_FUNDAMENTALS_PENDING"
            next_step = "COMPLETE_VERIFIED_FUNDAMENTALS_REQUIRED"
        elif not final_pass:
            state = "TRACKED_QUARTERLY_OR_FINAL_SCREEN_PENDING"
            next_step = "COMPLETE_QUARTERLY_AND_PRICE_HISTORY_REVIEW_REQUIRED"
        else:
            state = "EXISTING_STRICT_FUNDAMENTAL_SHORTLIST_ONLY"
            next_step = "INDEPENDENT_TECHNICAL_HISTORY_AND_FORWARD_VALIDATION_REQUIRED"
        output = {
            **row,
            "Baseline_Tracked": str(tracked).lower(),
            "Strict_Annual_Pass": str(annual_pass).lower(),
            "Strict_Final_Shortlist": str(final_pass).lower(),
            "First_Seen_UTC": first_seen,
            "New_Since_Prior_List": str(new).lower(),
            "Discovery_State": state,
            "Next_Evidence_Needed": next_step,
        }
        catalog.append(output)
        if not final_pass:
            queue.append(output)
    # If all steps above succeed, publish only research CSVs. Never change
    # history/strategy inputs, execution watchlist, broker state or credentials.
    write_csv_atomic(root / CATALOG, CATALOG_COLS, catalog)
    write_csv_atomic(root / QUEUE, QUEUE_COLS, queue)
    status = {
        "checked_at_utc": stamp, "state": "OFFICIAL_DISCOVERY_UPDATED",
        "mainboard_eq_count": len(confirmed), "sme_excluded": len(main)-len(confirmed),
        "sme_list_count": len(sme), "sme_isin_rows": len(sme_isins),
        "sme_exclusion_basis": "SYMBOL_AND_AVAILABLE_ISIN",
        "baseline_tracked_in_mainboard": sum(
            row["Baseline_Tracked"] == "true" for row in catalog
        ),
        "outside_annual_baseline": sum(row["Baseline_Tracked"] == "false" for row in catalog),
        "research_queue_count": len(queue),
        "new_or_isin_changed_since_last_snapshot": newly,
        "removed_since_last_snapshot": len(set(previous)-set(confirmed)),
        "equity_url": EQUITY_URL, "equity_sha256": hashlib.sha256(main_raw).hexdigest(),
        "sme_url": SME_URL, "sme_sha256": hashlib.sha256(sme_raw).hexdigest(),
        "read_only": True, "orders": "DISABLED",
        "automatic_trade_shortlist_promotion": "DISABLED",
        "historical_adjusted_backfill": "NOT_IMPLEMENTED_IN_THIS_DISCOVERY_STEP",
        "official_source_last_updated": "NOT_VERIFIED",
    }
    atomic_status(root / STATUS, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()
    try:
        report = refresh(data_root())
    except (DiscoveryUnavailable, requests.RequestException, OSError) as exc:
        report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
                  "state": "BLOCKED_OFFICIAL_DISCOVERY",
                  "reason": str(exc)[:160], "reports_preserved": True,
                  "orders": "DISABLED"}
        atomic_status(data_root() / STATUS, report)
        print(json.dumps(report, indent=2))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
