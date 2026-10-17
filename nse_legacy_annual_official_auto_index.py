#!/usr/bin/env python3
"""Bounded NSE OLD annual XBRL link metadata discovery for local legacy recovery.

The official NSE corporate-financial-results index is distinct from the
2025+ Integrated Filing feed. This collector can discover only candidate
original FY2022-23/FY2023-24 XBRL links. It NEVER approves numeric facts.
The separate legacy XML verifier must check exact audited context, source
units and issuer before any historical financial row can be added.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone, date
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import requests

from nse_annual_pdf_section_review import checked_sources, SectionReviewBlocked
from nse_mainboard_discovery import write_csv_atomic
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

API="https://www.nseindia.com/api/corporates-financial-results"
LANDING="https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
STATUS="runtime/nse_legacy_annual_official_auto_index_status.json"
LEDGER="runtime/nse_legacy_annual_official_auto_index_ledger.csv"
CROSSCHECK="nse_annual_pdf_vs_xbrl_source_review.csv"
YEARS={
    "2022-23":("2022-04-01","2023-03-31"),
    "2023-24":("2023-04-01","2024-03-31"),
}
INDEX_FIELDS=(
    "Symbol","ISIN","Company Name","Period Ended","Audited / Unaudited",
    "Consolidated / Non-Consolidated","Period","Reporting Period",
    "XBRL URL","Broadcast Date","Source_API","NSE_Original_Row_SHA256",
    "Indexed_At_UTC",
)
LEDGER_FIELDS=("Symbol","Checked_At_UTC","State","Rows_Indexed","Reason",
               "Diagnostic_Version")
DIAGNOSTIC_VERSION="legacy-nse-response-diagnostic-v2"
ONE_SAFE_REPROBE_DELAY=timedelta(minutes=10)
MAX_SYMBOLS=3
COOLDOWN=timedelta(hours=20)
MAX_RESPONSE_BYTES=1_800_000
MAX_ROWS_PER_SYMBOL=300
SYMBOL=re.compile(r"^[A-Z0-9&._-]{1,40}$")
ISIN=re.compile(r"^IN[A-Z0-9]{10}$")
GAP_STATES={
    "ORIGINAL_NSE_XBRL_HISTORY_NOT_AVAILABLE",
    "ORIGINAL_NSE_XBRL_HISTORY_NOT_SOURCE_VERIFIED",
    "AMBIGUOUS_XBRL_REVISIONS_OR_CONFLICTING_NUMERIC_FACTS",
}


class LegacyIndexUnavailable(RuntimeError):
    pass


def csv_rows(path,limit=60000):
    if not path.is_file():
        return []
    if path.is_symlink() or path.stat().st_size>18_000_000:
        raise LegacyIndexUnavailable("UNSAFE_LOCAL_INDEX_CSV")
    try:
        with path.open(encoding="utf-8-sig",newline="") as stream:
            reader=csv.DictReader(stream)
            if not reader.fieldnames:
                raise LegacyIndexUnavailable("LOCAL_INDEX_HEADER_MISSING")
            rows=[]
            for row in reader:
                if len(rows)>=limit or None in row:
                    raise LegacyIndexUnavailable("LOCAL_INDEX_SCHEMA_UNBOUNDED")
                rows.append(row)
            return rows
    except (OSError,UnicodeError,csv.Error) as exc:
        raise LegacyIndexUnavailable("LOCAL_INDEX_READ_FAILED") from exc


def official_link(raw):
    if not isinstance(raw,str) or len(raw)>650:
        return False
    try:
        u=urlsplit(raw)
        return (u.scheme=="https" and u.hostname=="nsearchives.nseindia.com"
                and u.port is None and u.username is None and u.password is None
                and not u.query and not u.fragment
                and u.path.startswith("/corporate/xbrl/")
                and u.path.lower().endswith(".xml")
                and ".." not in u.path)
    except ValueError:
        return False


def parse_day(raw):
    s=str(raw or "").strip()
    for fmt in ("%d-%b-%Y %H:%M:%S","%d-%b-%Y %H:%M",
                "%d-%b-%Y","%Y-%m-%d","%d-%B-%Y"):
        try:
            return datetime.strptime(s,fmt).date()
        except ValueError:
            pass
    return None


def source_metadata(row,symbol,isin,*,now):
    """Only explicit NSE Annual, Audited, Consolidated and exact FY dates."""
    if not isinstance(row,dict):
        return None
    if (row.get("symbol","").strip().upper()!=symbol
        or row.get("isin","").strip().upper()!=isin
        or not SYMBOL.fullmatch(symbol) or not ISIN.fullmatch(isin)
        or str(row.get("period","")).strip().casefold()!="annual"
        or str(row.get("audited","")).strip().casefold()!="audited"
        or str(row.get("consolidated","")).strip().casefold()!="consolidated"):
        return None
    begin=parse_day(row.get("fromDate"))
    end=parse_day(row.get("toDate"))
    year=next((fy for fy,(start,stop) in YEARS.items()
               if begin==date.fromisoformat(start) and
               end==date.fromisoformat(stop)),None)
    if not year or parse_day(row.get("broadCastDate")) is None:
        return None
    published=parse_day(row["broadCastDate"])
    if not end<=published<=now.date():
        return None
    url=row.get("xbrl","")
    if not official_link(url):
        return None
    # Reported financial-year label must be consistent if present. Never
    # infer fiscal dates from a mere 4th-quarter quarter-end result.
    label=str(row.get("financialYear","")).strip()
    if label:
        found=re.findall(r"\d{2}-[A-Za-z]{3}-\d{4}",label)
        if len(found)!=2 or parse_day(found[0])!=begin or parse_day(found[1])!=end:
            return None
    company=str(row.get("companyName","")).strip()
    if not company or len(company)>220:
        return None
    evidence=json.dumps(row,sort_keys=True,ensure_ascii=False,default=str).encode("utf-8")
    return year,{
        "Symbol":symbol,"ISIN":isin,"Company Name":company,
        "Period Ended":end.isoformat(),"Audited / Unaudited":"Audited",
        "Consolidated / Non-Consolidated":"Consolidated",
        "Period":"Annual","Reporting Period":"Annual",
        "XBRL URL":url,"Broadcast Date":published.isoformat(),
        "Source_API":API,"NSE_Original_Row_SHA256":hashlib.sha256(evidence).hexdigest(),
        "Indexed_At_UTC":now.astimezone(timezone.utc).isoformat(),
    }


def query(session,symbol):
    """At most one request; sanitized, distinguish network/HTML/JSON faults.

    Raw URL token/cookies, response HTML, body content and exception
    strings are NEVER written to logs. A source problem is not a missing
    XBRL record and is never a reason to weaken audited-fact checks.
    """
    if not SYMBOL.fullmatch(symbol):
        raise LegacyIndexUnavailable("INVALID_COMPANY_SYMBOL")
    try:
        response=session.get(
            API,params={"index":"equities","period":"Annual","symbol":symbol},
            headers={"Referer":LANDING,"Accept":"application/json, text/plain, */*"},
            allow_redirects=False,timeout=(12,30),
        )
    except requests.exceptions.SSLError as exc:
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_TLS_ERROR") from exc
    except requests.exceptions.ConnectTimeout as exc:
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_CONNECT_TIMEOUT") from exc
    except requests.exceptions.ReadTimeout as exc:
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_READ_TIMEOUT") from exc
    except requests.exceptions.ProxyError as exc:
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_PROXY_ERROR") from exc
    except requests.exceptions.ConnectionError as exc:
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_CONNECTION_ERROR") from exc
    except requests.RequestException as exc:
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_REQUEST_ERROR") from exc

    status=response.status_code
    if status in (403,429):
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_ACCESS_BLOCKED_"+str(status))
    if status!=200:
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_HTTP_"+str(status))
    body=response.content
    if len(body)>MAX_RESPONSE_BYTES:
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_RESPONSE_TOO_LARGE")
    if not body:
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_EMPTY_200_RESPONSE")
    # HTTP 200 with an HTML challenge is NOT a valid NSE source index.
    # Never save or display its content, headers or redirect destination.
    content_type=str(getattr(response,"headers",{}).get("Content-Type","")).lower()
    if "text/html" in content_type or body.lstrip()[:1]==b"<":
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_HTML_INSTEAD_OF_JSON")
    try:
        data=response.json()
    except (ValueError,requests.RequestException) as exc:
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_INVALID_JSON_200_RESPONSE") from exc
    if isinstance(data,list):
        rows=data
    elif isinstance(data,dict) and isinstance(data.get("data"),list):
        rows=data["data"]
    else:
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_SCHEMA_CHANGED")
    if len(rows)>MAX_ROWS_PER_SYMBOL or not all(isinstance(r,dict) for r in rows):
        raise LegacyIndexUnavailable("OFFICIAL_NSE_OLD_RESULTS_ROWS_UNBOUNDED")
    return rows


def prioritized_symbols(root,verified):
    """Require attested issuer/ISIN and exact archived FY source receipt."""
    issuer={}
    for receipt,entry,_ in verified:
        sym=receipt["Symbol"]
        if sym in issuer and issuer[sym]!=entry["ISIN"]:
            raise LegacyIndexUnavailable("CONFLICTING_VERIFIED_ISIN")
        issuer[sym]=entry["ISIN"]
    crosscheck=csv_rows(root/CROSSCHECK)
    missing={(r.get("Symbol"),r.get("Financial_Year"))
             for r in crosscheck if r.get("Review_State") in GAP_STATES}
    ranked={}
    for receipt,entry,_ in verified:
        sym,yr=receipt["Symbol"],receipt["Financial_Year"]
        ranked[sym]=min(ranked.get(sym,1),(0 if (sym,yr) in missing else 1))
    return sorted(issuer,key=lambda sym:(ranked[sym],sym)),issuer


def run(root,*,max_symbols=1,now=None,session=None,pause=time.sleep):
    if not 1<=max_symbols<=MAX_SYMBOLS:
        raise ValueError("MAX_SYMBOLS_BOUNDED_1_TO_3")
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_NOW_REQUIRED")
    verified=checked_sources(root,now)
    symbols,issuer=prioritized_symbols(root,verified)
    ledger=csv_rows(root/LEDGER)
    latest={r.get("Symbol"):r for r in ledger}
    eligible=[]
    deferred=[]
    for symbol in symbols:
        old=latest.get(symbol)
        if old:
            try:
                checked=datetime.fromisoformat(old["Checked_At_UTC"])
                age=(now-checked if checked.tzinfo is not None else None)
                if age is not None and timedelta(0)<=age<=COOLDOWN:
                    # One diagnostically improved re-probe of the previously
                    # ambiguous *non-403/429* error, after a 10 minute rest.
                    # Only a pre-v2 ledger can qualify. v2 entries retain
                    # normal 20-hour cooldown even if their probe fails.
                    one_safe_probe=(
                        old.get("State")=="INDEX_DISCOVERY_BLOCKED"
                        and old.get("Reason")=="OFFICIAL_NSE_OLD_RESULTS_NETWORK_OR_JSON_ERROR"
                        and not old.get("Diagnostic_Version")
                        and ONE_SAFE_REPROBE_DELAY<=age)
                    if not one_safe_probe:
                        # An eligible time is informative, NOT a new request.
                        # Leave full issuer backlog visible during cooldown.
                        earliest=(
                            checked+ONE_SAFE_REPROBE_DELAY
                            if (old.get("State")=="INDEX_DISCOVERY_BLOCKED"
                                and old.get("Reason")==
                                    "OFFICIAL_NSE_OLD_RESULTS_NETWORK_OR_JSON_ERROR"
                                and not old.get("Diagnostic_Version"))
                            else checked+COOLDOWN)
                        deferred.append((symbol,earliest))
                        continue
            except (ValueError,KeyError,TypeError):
                pass
        eligible.append(symbol)
    owner=session is None
    session=session or requests.Session()
    counts=dict(checked=0,new_index_rows=0,new_verified_facts=0,
                access_blocked=False,source_xml_downloads=0)
    errors=[]
    state=("OFFICIAL_NSE_OLD_ANNUAL_INDEX_COOLDOWN_NO_REQUEST"
           if deferred and not eligible
           else "OFFICIAL_NSE_OLD_ANNUAL_INDEX_NO_VERIFIED_ISSUERS"
           if not symbols
           else "OFFICIAL_NSE_OLD_ANNUAL_INDEX_UPDATED")
    try:
        for symbol in eligible[:max_symbols]:
            try:
                observations=query(session,symbol)
                approved=[]
                for source in observations:
                    record=source_metadata(source,symbol,issuer[symbol],now=now)
                    if record:
                        approved.append(record)
                for fy in YEARS:
                    index_path=root/f"nse_legacy_financial_results_{fy}.csv"
                    original=csv_rows(index_path)
                    current={(r.get("Symbol"),r.get("XBRL URL")):r
                             for r in original}
                    for year,rec in approved:
                        if year!=fy:
                            continue
                        key=(symbol,rec["XBRL URL"])
                        old=current.get(key)
                        if old:
                            # Never silently rewrite an older official filing
                            # receipt with newly observed conflicting metadata.
                            if any(old.get(k)!=rec[k] for k in (
                                "ISIN","Period Ended","Broadcast Date",
                                "NSE_Original_Row_SHA256")):
                                raise LegacyIndexUnavailable(
                                    "PREEXISTING_INDEX_RECEIPT_CONFLICT")
                        else:
                            current[key]=rec
                            counts["new_index_rows"]+=1
                    if current and len(current)>60000:
                        raise LegacyIndexUnavailable("INDEX_SIZE_LIMIT")
                    if current and (len(current)!=len(original) or
                                    not index_path.is_file()):
                        write_csv_atomic(index_path,list(INDEX_FIELDS),
                                         sorted(current.values(),key=lambda r:(
                                             r.get("Symbol",""),r.get("XBRL URL",""))))
                latest[symbol]=dict(Symbol=symbol,
                    Checked_At_UTC=now.astimezone(timezone.utc).isoformat(),
                    State="OFFICIAL_NSE_INDEX_CANDIDATES_ONLY",
                    Rows_Indexed=len(approved),Reason="",
                    Diagnostic_Version=DIAGNOSTIC_VERSION)
                counts["checked"]+=1
            except LegacyIndexUnavailable as exc:
                reason=str(exc)
                errors.append({"symbol":symbol,"reason":reason})
                latest[symbol]=dict(Symbol=symbol,
                    Checked_At_UTC=now.astimezone(timezone.utc).isoformat(),
                    State="INDEX_DISCOVERY_BLOCKED",Rows_Indexed=0,
                    Reason=reason,Diagnostic_Version=DIAGNOSTIC_VERSION)
                if "ACCESS_BLOCKED" in reason:
                    counts["access_blocked"]=True
                state="OFFICIAL_NSE_OLD_ANNUAL_INDEX_PARTIAL_OR_BLOCKED"
                # Any API/schema/index problem aborts remaining symbols:
                # never hammer NSE or write guessed candidate URL values.
                break
            pause(.35)
    finally:
        if owner:
            session.close()
    if latest:
        write_csv_atomic(root/LEDGER,list(LEDGER_FIELDS),
                         sorted(latest.values(),key=lambda r:r["Symbol"]))
    result={
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":state,
        "verified_issuer_symbols":len(symbols),
        "response_diagnostic_version":DIAGNOSTIC_VERSION,
        "api_calls_made_this_run":counts["checked"]+len(errors),
        "symbols_requested_this_run":min(max_symbols,len(eligible)),
        "symbols_fetched_this_run":counts["checked"],
        "symbols_deferred_by_cooldown":len(deferred),
        "next_eligible_at_utc":(
            min(earliest for _,earliest in deferred)
            .astimezone(timezone.utc).isoformat()
            if deferred else None
        ),
        # Include deferred issuers; a zero-request run is NOT completion.
        "symbols_remaining_after_this_run":(
            len(deferred)+max(
                0,len(eligible)-counts["checked"]-len(errors)
            )
        ),
        "new_official_old_annual_xbrl_links_indexed":counts["new_index_rows"],
        "recovered_audited_xbrl_facts":0,
        "requires_separate_original_xbrl_source_verification":True,
        "access_blocked_without_retry":counts["access_blocked"],
        "errors":errors,
        "orders":"DISABLED",
    }
    atomic_status(root/STATUS,result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-symbols",type=int,default=1)
    args=parser.parse_args()
    try:
        result=run(data_root(),max_symbols=args.max_symbols)
    except (SectionReviewBlocked,LegacyIndexUnavailable,OSError,ValueError,csv.Error) as exc:
        result={
            "state":"OFFICIAL_OLD_ANNUAL_INDEX_DISCOVERY_BLOCKED",
            "reason":str(exc)[:180],
            "existing_verified_financial_history_preserved":True,
            "orders":"DISABLED",
        }
        atomic_status(data_root()/STATUS,result)
        print(json.dumps(result,indent=2))
        return 1
    print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
