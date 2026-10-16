"""Resumable, bounded Upstox NSE_EQ company-fundamentals reference collector.

Reads an existing current vendor NSE_EQ symbol→ISIN map and only the two
local NSE annual baseline symbol sets. Independent, read-only vendor reference
data; NOT audited NSE XBRL, NOT historical point-in-time constituents, NOT a
strict fundamental pass or a trade recommendation. Does not place orders.

Private local token is loaded from ignored .env without printing it.

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_batches.py --batch 25

Repeated invocations process new symbols only. No aggressive concurrent
network calls. Each successfully fetched company is checkpointed atomically
so Termux interruption leaves completed reference reports intact.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import time

from dotenv import load_dotenv
import requests

from research_monitor.paths import CODE_ROOT, data_root
from research_monitor.schedule import atomic_status
from research_monitor.upstox_instrument_master import (
    MASTER_URL, MAP_NAME, METADATA_NAME,
)
from upstox_reference_pilot import (
    BlockedSource, ENDPOINTS, EQUITY_SYMBOL, ISIN_PATTERN,
    baseline_intersection, metrics, read_fundamental,
)

load_dotenv(CODE_ROOT / ".env",interpolate=False)

VERSION="upstox-reference-batches-v1"
FOLDER="upstox-nse-historical-reference-v1"
MAX_BATCH=25
MAX_SOURCE_AGE_DAYS=7
PAUSE_SECONDS=.30
SOFT_FAILURE_RETRY_HOURS=24
FATAL_ERRORS={
    "UPSTOX_HTTP_401","UPSTOX_HTTP_403","UPSTOX_HTTP_429",
    "UPSTOX_NETWORK_OR_SCHEMA_ERROR","UPSTOX_JSON_TOO_LARGE",
}


class ReferenceBatchError(RuntimeError):
    pass


def _source_dir(root):
    return Path(root)/"runtime"/"experiments"/FOLDER


def _json_object(path):
    try:
        data=json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError):
        raise ReferenceBatchError("LOCAL_REFERENCE_OR_MAPPING_JSON_INVALID") from None
    if not isinstance(data,dict):
        raise ReferenceBatchError("LOCAL_REFERENCE_OR_MAPPING_JSON_INVALID")
    return data


def verified_reference_map(root, now=None):
    """Validate cached vendor mapping's provenance, age, schema and membership.

    This does not certify official NSE sector, present or historical NSE
    mainboard listings, or any audited accounting fact.
    """
    root=Path(root)
    folder=_source_dir(root)
    meta=_json_object(folder/METADATA_NAME)
    if not (
        meta.get("scope")=="CURRENT_NSE_EQ_VENDOR_CROSSWALK_ONLY_NOT_MAINBOARD_ATTESTATION"
        and meta.get("source_url")==MASTER_URL
        and meta.get("data_source")=="UPSTOX_PUBLIC_NSE_BOD_JSON_GZIP"
        and meta.get("verified_mainboard_history") is False
        and meta.get("nse_xbrl_verified") is False
        and re.fullmatch(r"[0-9a-f]{64}",str(meta.get("source_sha256","")))
    ):
        raise ReferenceBatchError("UPSTOX_MAP_PROVENANCE_UNTRUSTED")
    now=now or datetime.now(timezone.utc)
    try:
        ts=datetime.fromisoformat(meta["fetched_at_utc"].replace("Z","+00:00"))
        if ts.tzinfo is None or not (
            timedelta(0)<=now-ts<=timedelta(days=MAX_SOURCE_AGE_DAYS)
        ):
            raise ReferenceBatchError("UPSTOX_MAP_STALE_OR_FUTURE_DATE")
    except (TypeError,ValueError,KeyError):
        raise ReferenceBatchError("UPSTOX_MAP_INVALID_TIMESTAMP") from None
    map_path=folder/MAP_NAME
    if meta.get("mapping_path")!=str(map_path):
        raise ReferenceBatchError("UPSTOX_MAP_PATH_MISMATCH")
    eligible=baseline_intersection(root)
    if not eligible:
        raise ReferenceBatchError("BOTH_NSE_ANNUAL_BASELINE_LISTS_REQUIRED")
    try:
        with map_path.open("r",encoding="utf-8",newline="") as stream:
            reader=csv.DictReader(stream)
            if reader.fieldnames != ["SYMBOL","ISIN","SERIES","SOURCE"]:
                raise ReferenceBatchError("VENDOR_MAP_SCHEMA_INVALID")
            rows=list(reader)
    except (OSError,UnicodeError,csv.Error):
        raise ReferenceBatchError("VENDOR_MAP_UNREADABLE") from None
    selected={}
    seen_isin={}
    for row in rows:
        sym,isin=row["SYMBOL"],row["ISIN"]
        if not (
            isinstance(sym,str) and EQUITY_SYMBOL.fullmatch(sym)
            and isinstance(isin,str) and ISIN_PATTERN.fullmatch(isin)
            and row["SERIES"]=="EQ"
            and row["SOURCE"]=="UPSTOX_NSE_BOD_CURRENT_VENDOR_MAP"
            and sym in eligible
        ):
            raise ReferenceBatchError("VENDOR_MAP_HAS_INELIGIBLE_INSTRUMENT")
        if sym in selected or isin in seen_isin:
            raise ReferenceBatchError("VENDOR_MAP_CONFLICT_OR_DUPLICATE")
        selected[sym]=isin
        seen_isin[isin]=sym
    if len(selected)!=meta.get("verified_by_vendor_crosswalk"):
        raise ReferenceBatchError("VENDOR_MAP_COUNT_MISMATCH")
    return selected,hashlib.sha256(map_path.read_bytes()).hexdigest()


def _stored_paths(root):
    folder=_source_dir(root)/VERSION
    records=folder/"records"
    return folder,records,folder/"last_batch.json"


def _failed_issuer_status(folder, mapping, now=None):
    """Keep soft failures from monopolizing the next resumed batch.

    Expired entries are rechecked automatically after a 24-hour cooldown.
    Authentication, rate-limit and network failures are never treated as
    successful or permanent individual-company failures.
    """
    path=folder/"soft_failures.json"
    if not path.exists():
        return {},{}
    payload=_json_object(path)
    if payload.get("scope")!="UPSTOX_VENDOR_SOFT_FAILURE_COOLDOWN":
        raise ReferenceBatchError("INVALID_VENDOR_FAILURE_LEDGER")
    entries=payload.get("issues")
    if not isinstance(entries,dict):
        raise ReferenceBatchError("INVALID_VENDOR_FAILURE_LEDGER")
    now=now or datetime.now(timezone.utc)
    deferred={}
    kept={}
    for symbol,row in entries.items():
        if not (
            isinstance(symbol,str) and symbol in mapping
            and isinstance(row,dict) and row.get("isin")==mapping[symbol]
            and isinstance(row.get("status"),str)
            and row["status"].startswith(("UPSTOX_REFERENCE_HTTP_",
                                          "UPSTOX_CONSOLIDATED_YEARLY_",
                                          "UPSTOX_DATA_SCHEMA_UNEXPECTED"))
            and re.fullmatch(r"[A-Z0-9_]+",row["status"])
            and isinstance(row.get("at_utc"),str)
        ):
            raise ReferenceBatchError("INVALID_OR_STALE_VENDOR_FAILURE_LEDGER")
        try:
            stamp=datetime.fromisoformat(row["at_utc"].replace("Z","+00:00"))
        except ValueError:
            raise ReferenceBatchError("INVALID_VENDOR_FAILURE_DATE") from None
        if stamp.tzinfo is None or stamp>now:
            raise ReferenceBatchError("INVALID_VENDOR_FAILURE_DATE")
        kept[symbol]=row
        if now-stamp < timedelta(hours=SOFT_FAILURE_RETRY_HOURS):
            deferred[symbol]=row
    return kept,deferred


def _write_failures(folder, entries):
    path=folder/"soft_failures.json"
    atomic_status(path,{
        "scope":"UPSTOX_VENDOR_SOFT_FAILURE_COOLDOWN",
        "issues":entries,
        "policy":"Retry non-auth non-network soft failures after 24 hours",
    })
    os.chmod(path,0o600)


def _existing_records(records, mapping):
    completed={}
    if not records.exists():
        return completed
    for file in records.glob("*.json"):
        if not EQUITY_SYMBOL.fullmatch(file.stem):
            raise ReferenceBatchError("UNKNOWN_REFERENCE_RECORD_FILENAME")
        row=_json_object(file)
        sym=file.stem
        if not (
            sym in mapping
            and row.get("symbol")==sym
            and row.get("isin")==mapping[sym]
            and row.get("scope")=="UPSTOX_CURRENT_NSE_EQ_VENDOR_REFERENCE"
            and row.get("status")=="UPSTOX_REFERENCE_ONLY_NOT_NSE_VERIFIED"
            and row.get("is_nse_audited_verified") is False
            and row.get("nse_sector_verified") is False
            and isinstance(row.get("financials"),dict)
        ):
            raise ReferenceBatchError("REFERENCE_RECORD_REQUIRES_MANUAL_AUDIT")
        completed[sym]=row
    return completed


def _load_seed_report(folder):
    path=folder/"report.json"
    if not path.is_file():
        return []
    report=_json_object(path)
    if report.get("scope")!="NSE_MAINBOARD_UPSTOX_SECONDARY_VENDOR_REFERENCE_ONLY":
        return []
    result=report.get("results")
    return result if isinstance(result,list) else []


def _checkpoint_one(records, mapping, sym, payload, *, checked_at=None):
    """Atomic save of *complete* vendor-only data, never an NSE pass."""
    isin=mapping[sym]
    if not (
        payload.get("symbol")==sym and payload.get("isin")==isin
        and payload.get("status")=="UPSTOX_REFERENCE_ONLY_NOT_NSE_VERIFIED"
        and payload.get("is_nse_audited_verified") is False
        and payload.get("nse_sector_verified") is False
        and isinstance(payload.get("financials"),dict)
    ):
        raise ReferenceBatchError("UNTRUSTED_VENDOR_REFERENCE_RECORD")
    record={
        **payload,
        "scope":"UPSTOX_CURRENT_NSE_EQ_VENDOR_REFERENCE",
        "checked_at_utc":checked_at or datetime.now(timezone.utc).isoformat(),
        "strict_screen_approved":False,
        "trade_eligible":False,
    }
    path=records/(sym+".json")
    atomic_status(path,record)
    os.chmod(path,0o600)
    return record


def _summary(root, mapping, completed, *, run_results, fatal, requests_made,
             map_sha256, already_complete, seeded, tried,
             soft_failures, cooldown_skipped):
    all_rows=list(completed.values())
    return {
        "scope":"NSE_EQ_UPSTOX_SECONDARY_VENDOR_INCREMENTAL_REFERENCE_ONLY",
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "mapping_symbols":len(mapping),
        "mapping_sha256":map_sha256,
        "completed_company_records":len(all_rows),
        "remaining_uncollected":len(mapping)-len(all_rows),
        "previous_company_records":already_complete,
        "restored_from_previously_verified_vendor_pilot":seeded,
        "requested_new_companies":tried,
        "cached_soft_failures":soft_failures,
        "soft_failure_companies_skipped_during_cooldown":cooldown_skipped,
        "soft_failure_retry_after_hours":SOFT_FAILURE_RETRY_HOURS,
        "requests_this_run":requests_made,
        "new_company_records":len(run_results),
        "batch_results":run_results,
        "stop_reason":fatal,
        "fy2022_23_vendor_reference_complete":sum(
            r.get("fy2022_23_reference_complete") is True for r in all_rows),
        "fy2023_24_vendor_reference_complete":sum(
            r.get("fy2023_24_reference_complete") is True for r in all_rows),
        "vendor_sector_present":sum(
            bool(r.get("vendor_sector_reference")) for r in all_rows),
        "nse_audited_xbrl_records_added":0,
        "nse_official_sector_records_added":0,
        "strict_fundamental_pass_changes":0,
        "strategy_trading_enabled":False,
        "live_orders":"DISABLED",
        "limitations":[
            "Current Upstox NSE_EQ instrument list is not an independent NSE mainboard history.",
            "Vendor financial figures are NOT verified audited original NSE XBRL.",
            "Vendor sector labels are not official NSE sector classifications.",
            "Partial records are not marked complete and can be retried later.",
            "No verified strategy exists; no alerts, yes/no trade authorizations or orders.",
        ]
    }


def collect(root, token, batch=MAX_BATCH, *, session=None, pause=time.sleep,
            mapping_loader=verified_reference_map, seed_existing=True):
    """At most batch *new companies* per run, read-only, checkpoint on success.

    HTTP 401/403/429, network and 5xx stop; other missing vendor records
    remain uncompleted and are eligible for a new bounded trial next time.
    """
    if not isinstance(batch,int) or isinstance(batch,bool) or not 1<=batch<=MAX_BATCH:
        raise ValueError("BATCH_MUST_BE_1_TO_25")
    if not isinstance(token,str) or not token.strip():
        raise ReferenceBatchError("UPSTOX_ANALYTICS_TOKEN_MISSING")
    root=Path(root)
    mapping,digest=mapping_loader(root)
    if not mapping:
        raise ReferenceBatchError("NO_VALID_CURRENT_NSE_EQ_VENDOR_MAPPING")
    folder,records,status_path=_stored_paths(root)
    records.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(records,0o700)
    completed=_existing_records(records,mapping)
    soft_issues,deferred=_failed_issuer_status(folder,mapping)
    existing_before=len(completed)
    seeded=0
    # Reuse previously fetched vendor reference sample, *only* if its symbol
    # and ISIN match current filtered mapping. Never seed incomplete records.
    if seed_existing:
        for row in _load_seed_report(_source_dir(root)):
            sym=row.get("symbol")
            if sym in mapping and sym not in completed and row.get("isin")==mapping[sym]:
                if (
                    row.get("status")=="UPSTOX_REFERENCE_ONLY_NOT_NSE_VERIFIED"
                    and row.get("is_nse_audited_verified") is False
                    and row.get("nse_sector_verified") is False
                    and isinstance(row.get("financials"),dict)
                ):
                    completed[sym]=_checkpoint_one(records,mapping,sym,row)
                    seeded+=1

    pending=sorted(
        (s for s in mapping if s not in completed and s not in deferred),
        key=lambda s:hashlib.sha256(s.encode()).hexdigest()
    )[:batch]
    own=session is None
    session=session or requests.Session()
    run_results=[]
    requests_made=0
    stop_reason=None
    try:
        for symbol in pending:
            payloads={}
            failed=None
            for kind in ENDPOINTS:
                try:
                    payloads[kind]=read_fundamental(
                        session,token,mapping[symbol],kind
                    )
                    requests_made+=1
                except BlockedSource as exc:
                    failed=str(exc)
                    requests_made+=1
                    break
                pause(PAUSE_SECONDS)
            if failed is not None:
                # Not a complete company record: do not write to the cache.
                run_results.append({"symbol":symbol,"status":failed})
                if failed in FATAL_ERRORS or failed.startswith(
                    "UPSTOX_REFERENCE_HTTP_5"
                ):
                    stop_reason=failed
                    break
                soft_issues[symbol]={
                    "isin":mapping[symbol],
                    "status":failed,
                    "at_utc":datetime.now(timezone.utc).isoformat(),
                }
                _write_failures(folder,soft_issues)
                continue
            row=metrics(mapping[symbol],payloads)
            row["symbol"]=symbol
            row["status"]="UPSTOX_REFERENCE_ONLY_NOT_NSE_VERIFIED"
            completed[symbol]=_checkpoint_one(records,mapping,symbol,row)
            if symbol in soft_issues:
                del soft_issues[symbol]
                _write_failures(folder,soft_issues)
            run_results.append({
                "symbol":symbol,
                "status":row["status"],
                "fy2022_23_reference_complete":
                    row["fy2022_23_reference_complete"],
                "fy2023_24_reference_complete":
                    row["fy2023_24_reference_complete"],
                "vendor_sector_reference":row["vendor_sector_reference"],
            })
    finally:
        if own:
            session.close()
    summary=_summary(
        root,mapping,completed,
        run_results=run_results,fatal=stop_reason,
        requests_made=requests_made,map_sha256=digest,
        already_complete=existing_before,
        seeded=seeded,tried=len(pending),
        soft_failures=len(soft_issues),
        cooldown_skipped=len([
            s for s in deferred if s in mapping and s not in completed
        ]),
    )
    atomic_status(status_path,summary)
    os.chmod(status_path,0o600)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch",type=int,default=MAX_BATCH,
                        help="Maximum NEW issuers this run, 1..25")
    parser.add_argument("--no-seed",action="store_true",
                        help="Do not reuse prior matching vendor pilot observations")
    args=parser.parse_args()
    if not 1<=args.batch<=MAX_BATCH:
        parser.error("--batch must be 1..25")
    root=data_root()
    token=os.environ.get("UPSTOX_ANALYTICS_TOKEN","")
    try:
        result=collect(root,token,args.batch,seed_existing=not args.no_seed)
    except ReferenceBatchError as exc:
        # An error is always a fixed status; never print credentials.
        raise SystemExit(str(exc)) from None
    keys=[
        "scope","mapping_symbols","completed_company_records",
        "remaining_uncollected","restored_from_previously_verified_vendor_pilot",
        "new_company_records","requested_new_companies","requests_this_run",
        "cached_soft_failures","soft_failure_companies_skipped_during_cooldown",
        "stop_reason","fy2022_23_vendor_reference_complete",
        "fy2023_24_vendor_reference_complete","vendor_sector_present",
        "strict_fundamental_pass_changes","live_orders",
    ]
    print(json.dumps({key:result[key] for key in keys},indent=2))
    print("Private batch report:",_stored_paths(root)[2])


if __name__=="__main__":
    main()
