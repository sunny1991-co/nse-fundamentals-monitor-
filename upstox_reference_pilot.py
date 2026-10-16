"""Read-only NSE-company Upstox historical fundamental/sector REFERENCE pilot.

The user's NSE-mainboard-only scope is unchanged. Upstox fetches by an ISIN
mapped from a local NSE equity-list CSV (EQ series). It is an independent
SECONDARY vendor reference, NEVER an audited NSE XBRL source or an automatic
sector-screen PASS. No candidate, alert, broker orders or portfolio writes.

    cd ~/nse-fundamentals-monitor-
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_pilot.py --limit 5

Looks for EQUITY_L*.csv under NSE_DATA_DIR and Android's Download directory.
With --sync-instruments, downloads the PUBLIC Upstox NSE BOD instrument master
(HTTPS GET, no token), builds a current NSE_EQ + EQ-only ISIN reference map
cross-checked against BOTH local annual baseline symbol sets, then tests five
distinct matched issuers. This current vendor map is NOT historical NSE
constituent/mainboard evidence or audited XBRL proof.
Without a mapping, uses the documented NSE RELIANCE example only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

from dotenv import load_dotenv
import requests

from research_monitor.paths import CODE_ROOT, data_root
from research_monitor.schedule import atomic_status
from research_monitor.upstox_instrument_master import (
    InstrumentMapUnavailable, sync as sync_instrument_mapping,
)

load_dotenv(CODE_ROOT / ".env", interpolate=False)

VERSION = "upstox-nse-historical-reference-v1"
API_ROOT = "https://api.upstox.com/v2"
ISIN_PATTERN = re.compile(r"^IN[A-Z0-9]{10}$")
EQUITY_SYMBOL = re.compile(r"^[A-Z0-9&._-]{1,40}$")
FOUR_PERIODS = ("Mar 2023", "Mar 2024", "Mar 2025", "Mar 2026")
FALLBACK_EXAMPLE = ("RELIANCE", "INE002A01018")
ENDPOINTS = ("profile", "income-statement", "balance-sheet", "cash-flow")


def csv_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def linked_symbols(root):
    """Only independently sourced symbol/ISIN mapping can support multi-issuer pilot.

    Index equity list identifies current EQ symbols but does not independently
    attest their historical constituents or their audited statement basis.
    """
    root = Path(root)
    home = Path.home()
    search_folders = (
        root, home / "storage" / "shared" / "Download",
        Path("/storage/emulated/0/Download"), CODE_ROOT,
    )
    files = []
    seen = set()
    for folder in search_folders:
        if folder.exists() and folder.is_dir():
            for path in sorted(folder.glob("EQUITY_L*.csv")):
                if path.is_file() and str(path.resolve()) not in seen:
                    seen.add(str(path.resolve()))
                    files.append(path)
    return files


def baseline_intersection(root):
    root = Path(root)
    a, b = root / "FY2425.csv", root / "NSE_FY2025-26_Consolidated_Fundamentals.csv"
    if not a.is_file() or not b.is_file():
        return set()
    sets = []
    for filename in (a, b):
        sets.append({
            str(row.get("Symbol", "")).strip().upper()
            for row in csv_rows(filename)
            if str(row.get("Symbol", "")).strip()
        })
    return sets[0] & sets[1]


def choose_symbols(root, limit, mapping=None):
    if isinstance(limit, bool) or not isinstance(limit,int) or not 1 <= limit <= 5:
        raise ValueError("PILOT_MAX_5")
    chosen = {}
    conflicts = set()
    paths = [Path(mapping)] if mapping is not None else linked_symbols(root)
    eligible = baseline_intersection(root)
    source_path = ""
    for path in paths:
        if not path.is_file():
            continue
        try:
            rows = csv_rows(path)
        except (OSError,UnicodeDecodeError,csv.Error):
            continue
        if not rows or not {"SYMBOL","ISIN"}.issubset({
                str(k).strip().upper() for k in rows[0]}):
            continue
        source_path = path.name
        for row in rows:
            upper = {str(k).strip().upper(): str(v or "").strip().upper()
                     for k,v in row.items()}
            symbol, isin = upper.get("SYMBOL",""),upper.get("ISIN","")
            series=upper.get("SERIES","EQ")
            if (series != "EQ" or not EQUITY_SYMBOL.fullmatch(symbol)
                    or not ISIN_PATTERN.fullmatch(isin)):
                continue
            if eligible and symbol not in eligible:
                continue
            if symbol in conflicts:
                continue
            if symbol in chosen and chosen[symbol] != isin:
                # Do not allow a later third row to resurrect a conflict.
                chosen.pop(symbol,None)
                conflicts.add(symbol)
                continue
            chosen[symbol] = isin
        if chosen:
            break
    if chosen:
        symbols = sorted(chosen, key=lambda s: hashlib.sha256(
            s.encode("utf-8")).hexdigest())[:limit]
        provenance = (
            "UPSTOX_CURRENT_NSE_EQ_BOD_VENDOR_MAP_NOT_MAINBOARD_ATTESTED"
            if source_path == "upstox_nse_eq_isin_map.csv" else
            "LOCAL_NSE_EQ_CSV_SOURCE_NOT_INDEPENDENTLY_AUTHENTICATED"
        )
        return [(s,chosen[s]) for s in symbols], (
            provenance+":"+source_path
        )
    if mapping is not None:
        return [], "EXPLICIT_MAPPING_EMPTY_OR_NOT_VERIFIED"
    return [FALLBACK_EXAMPLE], "DOCUMENTED_RELIANCE_EXAMPLE_ONLY_NO_MAPPING_FILE"


class BlockedSource(RuntimeError):
    pass


def read_fundamental(session, token, isin, kind):
    """Only documented Upstox GET. Never print tokens or raw API failures."""
    if not ISIN_PATTERN.fullmatch(isin) or kind not in ENDPOINTS:
        raise ValueError("ISIN_OR_ENDPOINT_UNACCEPTABLE")
    url = f"{API_ROOT}/fundamentals/{isin}/{kind}"
    params = (
        {"type":"consolidated","fs":"true","time_period":"yearly"}
        if kind == "income-statement" else
        {"type":"consolidated","fs":"true"}
        if kind in ("balance-sheet","cash-flow") else
        None
    )
    try:
        response = session.get(
            url, params=params,timeout=20,allow_redirects=False,
            headers={
                "Accept":"application/json",
                "Authorization":"Bearer "+token,
            },
        )
        if response.status_code != 200:
            code=response.status_code
            if code in (401,403,429):
                raise BlockedSource("UPSTOX_HTTP_"+str(code))
            raise BlockedSource("UPSTOX_REFERENCE_HTTP_"+str(code))
        if len(response.content)>2_000_000:
            raise BlockedSource("UPSTOX_JSON_TOO_LARGE")
        body=response.json()
    except BlockedSource:
        raise
    except (requests.RequestException,ValueError,TypeError,OverflowError):
        raise BlockedSource("UPSTOX_NETWORK_OR_SCHEMA_ERROR") from None
    if (not isinstance(body,dict) or body.get("status") != "success"
            or not isinstance(body.get("data"),dict)):
        raise BlockedSource("UPSTOX_DATA_SCHEMA_UNEXPECTED")
    data=body["data"]
    if kind != "profile" and (
        data.get("type")!="consolidated"
        or data.get("units_in")!="crore"
        or data.get("time_period")!="yearly"
    ):
        raise BlockedSource("UPSTOX_CONSOLIDATED_YEARLY_UNITS_NOT_VERIFIED")
    return data


def _year_hist(entries):
    result = {}
    if not isinstance(entries,list):
        return result
    for item in entries:
        if not isinstance(item,dict):
            continue
        period=item.get("period")
        value=item.get("value")
        if period not in FOUR_PERIODS or isinstance(value,bool):
            continue
        try:
            number=float(value)
        except (TypeError,ValueError):
            continue
        if math.isfinite(number):
            result[period]=round(number,5)
    return result


def _category_history(data, field, category):
    entries=data.get(field)
    if not isinstance(entries,list):
        return {}
    matches=[entry for entry in entries if isinstance(entry,dict)
             and entry.get("category")==category]
    if len(matches)!=1:
        return {}
    return _year_hist(matches[0].get("history"))


def metrics(isin, payloads):
    profile=payloads["profile"]
    balance=payloads["balance-sheet"]
    income=payloads["income-statement"]
    cash=payloads["cash-flow"]
    sector=str(profile.get("sector") or "").strip()
    # Vendor sector must remain distinct from official NSE classification.
    balance_hist=balance.get("history") or []
    assets=_year_hist([
        {"period":row.get("period"),"value":row.get("total_asset")}
        for row in balance_hist if isinstance(row,dict)
    ])
    operating=_category_history(cash,"cash_flow","operating")
    net_profit=_category_history(income,"income_statement","net_profit")
    revenue=_category_history(income,"income_statement","revenue")
    years={}
    for label in FOUR_PERIODS:
        years[label]={
            "income_revenue_vendor_crore":revenue.get(label),
            "income_net_profit_vendor_crore":net_profit.get(label),
            "balance_total_assets_vendor_crore":assets.get(label),
            "operating_cash_flow_vendor_crore":operating.get(label),
        }
    return {
        "isin":isin,
        "vendor_sector_reference":sector,
        "financials":years,
        "fy2022_23_reference_complete":all(
            years["Mar 2023"][key] is not None for key in years["Mar 2023"]
        ),
        "fy2023_24_reference_complete":all(
            years["Mar 2024"][key] is not None for key in years["Mar 2024"]
        ),
        "report_basis":"UPSTOX_API_ASSERTS_CONSOLIDATED_NOT_XBRL_VERIFIED",
        "is_nse_audited_verified":False,
        "nse_sector_verified":False,
    }


def run(root, token, limit=5, *, mapping=None, session=None, pause=time.sleep):
    if not token or not isinstance(token,str):
        raise ValueError("UPSTOX_ANALYTICS_TOKEN_NOT_CONFIGURED")
    selected,mapping_status=choose_symbols(root,limit,mapping)
    report={
        "scope":"NSE_MAINBOARD_UPSTOX_SECONDARY_VENDOR_REFERENCE_ONLY",
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "mapping_status":mapping_status,
        "tested_symbols":len(selected),
        "source":"https://api.upstox.com/v2/fundamentals/:isin",
        "results":[],
        "requests_success":0,
        "stopped_for_api_block":False,
        "nse_xbrl_verified_years_added":0,
        "nse_official_sectors_added":0,
        "annual_strict_pass_changes":0,
        "trade_candidates_created":0,
        "live_orders":"DISABLED",
        "data_source_ranking":["GROWW_PRIMARY_MARKET_DATA",
                               "UPSTOX_SECONDARY_REFERENCE"],
        "notes":[
            "Upstox is a broker with NSE/BSE membership, but only NSE EQ ISIN mappings are selected.",
            "Historical Upstox reports are reference data, not verified original NSE audited XBRL.",
            "A vendor sector label does not satisfy official NSE sector classification.",
            "Missing periods or mismatched statement type fail closed; no standalone substitution.",
            "No automatic trading, recommendation, or alteration of original NSE CSV records.",
        ],
    }
    owns=session is None
    session=session or requests.Session()
    try:
        for symbol,isin in selected:
            payloads={}
            failed=None
            for kind in ENDPOINTS:
                try:
                    payloads[kind]=read_fundamental(session,token,isin,kind)
                    report["requests_success"]+=1
                except BlockedSource as exc:
                    failed=str(exc)
                    if failed in ("UPSTOX_HTTP_401","UPSTOX_HTTP_403","UPSTOX_HTTP_429"):
                        report["stopped_for_api_block"]=True
                    break
                pause(.2)
            if failed:
                report["results"].append({
                    "symbol":symbol,"isin":isin,"status":failed,
                    "is_nse_audited_verified":False,
                })
            else:
                record=metrics(isin,payloads)
                record["symbol"]=symbol
                record["status"]="UPSTOX_REFERENCE_ONLY_NOT_NSE_VERIFIED"
                report["results"].append(record)
            if report["stopped_for_api_block"]:
                break
    finally:
        if owns:
            session.close()
    report["status_counts"]=dict(Counter(r["status"] for r in report["results"]))
    report["fy2022_23_reference_complete_symbols"]=sum(
        r.get("fy2022_23_reference_complete") is True for r in report["results"]
    )
    report["fy2023_24_reference_complete_symbols"]=sum(
        r.get("fy2023_24_reference_complete") is True for r in report["results"]
    )
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit",type=int,default=5)
    parser.add_argument("--mapping",help="Optional user-provided NSE EQ symbol/ISIN CSV")
    parser.add_argument(
        "--sync-instruments",action="store_true",
        help="Download public Upstox NSE BOD master, use matched NSE_EQ + EQ ISINs"
    )
    args=parser.parse_args()
    if not 1<=args.limit<=5:
        parser.error("--limit must be between 1 and 5")
    if args.mapping and args.sync_instruments:
        parser.error("Use either --mapping OR --sync-instruments")
    root=data_root()
    token=os.environ.get("UPSTOX_ANALYTICS_TOKEN","")
    if not token:
        raise SystemExit("UPSTOX_ANALYTICS_TOKEN_NOT_CONFIGURED_IN_LOCAL_ENV")
    mapping=args.mapping
    instrument_summary=None
    if args.sync_instruments:
        try:
            instrument_summary=sync_instrument_mapping(root)
        except InstrumentMapUnavailable as exc:
            # Fail closed: do not convert a failed 5-stock sync into another
            # one-RELIANCE apparent success.
            raise SystemExit(str(exc)) from None
        mapping=instrument_summary["mapping_path"]
    report=run(root,token,args.limit,mapping=mapping)
    if instrument_summary is not None:
        report["current_vendor_instrument_mapping"]={
            k:instrument_summary[k] for k in (
                "overlapping_baseline_symbols",
                "nse_segment_eq_rows",
                "verified_by_vendor_crosswalk",
                "unmatched_baseline_intersection",
                "conflicting_symbols","conflicting_isins",
                "source_sha256",
                "verified_mainboard_history",
                "nse_xbrl_verified",
            )
        }
    folder=root/"runtime"/"experiments"/VERSION
    folder.mkdir(parents=True,exist_ok=True)
    atomic_status(folder/"report.json",report)
    print(json.dumps({
        "scope":report["scope"],
        "mapping_status":report["mapping_status"],
        "current_vendor_instrument_mapping":report.get(
            "current_vendor_instrument_mapping"
        ),
        "tested_symbols":report["tested_symbols"],
        "requests_success":report["requests_success"],
        "stopped_for_api_block":report["stopped_for_api_block"],
        "status_counts":report["status_counts"],
        "fy2022_23_reference_complete_symbols":
            report["fy2022_23_reference_complete_symbols"],
        "fy2023_24_reference_complete_symbols":
            report["fy2023_24_reference_complete_symbols"],
        "sample": [
            {"symbol":r["symbol"],"status":r["status"],
             "vendor_sector_reference":r.get("vendor_sector_reference"),
             "fy2022_23_reference_complete":r.get("fy2022_23_reference_complete"),
             "fy2023_24_reference_complete":r.get("fy2023_24_reference_complete")}
            for r in report["results"]
        ],
        "annual_strict_pass_changes":0,
        "live_orders":"DISABLED",
    },indent=2))
    print("Private Upstox reference audit:",folder/"report.json")


if __name__=="__main__":
    main()
