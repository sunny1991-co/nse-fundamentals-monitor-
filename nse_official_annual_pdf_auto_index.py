#!/usr/bin/env python3
"""Daily read-only, source-linked NSE Annual Report PDF candidate acquisition.

This automates *finding and archiving original annual-report PDF candidates*
across newly discovered NSE Mainboard companies. PDF headers and NSE metadata
DO NOT verify audited/consolidated financial facts. Never changes fundamentals,
point-in-time strategy files, watchlists, SMS or broker orders.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

import requests

from nse_mainboard_discovery import CATALOG, write_csv_atomic
from research_monitor.discovered_annual_universe import verified_discovered_symbols
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

API = "https://www.nseindia.com/api/annual-reports"
LANDING = "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"
HOST = "nsearchives.nseindia.com"
OUTPUT = "nse_official_annual_pdf_candidates.csv"
LEDGER = "runtime/nse_official_annual_pdf_index_ledger.csv"
STATUS = "runtime/nse_official_annual_pdf_collector_status.json"
CACHED_STATUS = "runtime/nse_official_annual_pdf_cached_intake_status.json"
ACCESS_COOLDOWN = timedelta(hours=20)
PDF_DIR = Path("runtime") / "official_annual_report_pdf_candidates"
PDF_LOG = "runtime/nse_official_pdf_download_receipts.csv"
REQUIRED_YEARS = {"2022-23","2023-24"}
OUTPUT_FIELDS = ["Symbol","ISIN","Company","Financial_Year","From_Year","To_Year",
                 "Official_Report_URL","NSE_Reported_Company","Submission_Type",
                 "Broadcast_Date","Original_Metadata_SHA256","Indexed_At_UTC",
                 "Verification_State"]
LEDGER_FIELDS = ["Symbol","Checked_At_UTC","Status","Matching_PDF_Count"]
PDF_FIELDS = ["Symbol","Financial_Year","Official_Report_URL",
              "Source_Index_SHA256","PDF_SHA256","PDF_Bytes",
              "Cached_Private_Path","Downloaded_At_UTC","Review_Status"]
CORE_FUNDAMENTAL_FIELDS = (
    "Revenue_Operations_Cr", "Net_Profit_Cr", "Basic_EPS_Rs",
    "Total_Assets_Cr", "Total_Equity_Cr", "Total_Borrowings_Cr",
)
PRIORITY_SOURCE_FILES = (
    "FY2425.csv", "NSE_FY2025-26_Consolidated_Fundamentals.csv",
)
MAX_SYMBOLS = 50
MAX_DOWNLOADS = 2
MAX_PDF_BYTES = 26*1024*1024
MAX_PDF_STORAGE_BYTES = 1024*1024*1024
MIN_AVAILABLE_BYTES = 512*1024*1024
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9&._-]{1,40}$")


class OfficialAnnualPdfUnavailable(RuntimeError):
    pass


def read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig",newline="") as file:
        return list(csv.DictReader(file))


def source_pdf(url: str, symbol: str) -> bool:
    if not isinstance(url,str) or len(url)>600 or not SYMBOL_PATTERN.fullmatch(symbol):
        return False
    try:
        u=urlsplit(url)
        return (
            u.scheme=="https" and u.hostname==HOST and u.port is None
            and u.username is None and u.password is None
            and not u.query and not u.fragment
            and u.path.startswith("/annual_reports/")
            and u.path.lower().endswith(".pdf")
            and ("_"+symbol.upper()+"_") in u.path.upper()
            and ".." not in u.path
        )
    except ValueError:
        return False


def parse_metadata(payload, symbol:str, isin:str, company:str,
                   fetched_at:str)->list[dict]:
    if not isinstance(payload,dict) or not isinstance(payload.get("data"),list):
        raise OfficialAnnualPdfUnavailable("OFFICIAL_ANNUAL_API_RESPONSE_SCHEMA_CHANGED")
    observed=[]
    for data in payload["data"]:
        if not isinstance(data,dict):
            raise OfficialAnnualPdfUnavailable("OFFICIAL_ANNUAL_API_MALFORMED_ROW")
        try:
            from_year=int(data.get("fromYr"))
            to_year=int(data.get("toYr"))
        except (ValueError,TypeError):
            # Some submissions have missing year information, never infer it.
            continue
        if not (2020<=from_year<=2030 and to_year==from_year+1):
            continue
        fy=f"{from_year}-{str(to_year)[-2:]}"
        if fy not in REQUIRED_YEARS:
            continue
        url=data.get("fileName","")
        if not source_pdf(url,symbol):
            continue
        content=json.dumps(data,sort_keys=True,ensure_ascii=False,default=str).encode("utf-8")
        observed.append({
            "Symbol":symbol,"ISIN":isin,"Company":company,
            "Financial_Year":fy,"From_Year":from_year,"To_Year":to_year,
            "Official_Report_URL":url,
            "NSE_Reported_Company":str(data.get("companyName") or "")[:250],
            "Submission_Type":str(data.get("submission_type") or data.get("submissionType") or "")[:80],
            "Broadcast_Date":str(data.get("broadcast_dttm") or "")[:70],
            "Original_Metadata_SHA256":hashlib.sha256(content).hexdigest(),
            "Indexed_At_UTC":fetched_at,
            "Verification_State":"OFFICIAL_PDF_CANDIDATE_UNAUDITED_NUMERIC_FACTS",
        })
    return observed


def query_metadata(session: requests.Session, symbol: str):
    r=session.get(API,params={"index":"equities","symbol":symbol},
                  headers={"Referer":LANDING,"Accept":"application/json, text/plain, */*"},
                  timeout=25,allow_redirects=False)
    if r.status_code in (403,429):
        raise OfficialAnnualPdfUnavailable("NSE_ANNUAL_API_ACCESS_BLOCKED_"+str(r.status_code))
    if r.status_code!=200:
        raise OfficialAnnualPdfUnavailable("NSE_ANNUAL_API_HTTP_"+str(r.status_code))
    if len(r.content)>2_000_000:
        raise OfficialAnnualPdfUnavailable("NSE_ANNUAL_API_RESPONSE_TOO_LARGE")
    try:
        return r.json()
    except (ValueError,TypeError) as exc:
        raise OfficialAnnualPdfUnavailable("NSE_ANNUAL_API_INVALID_JSON") from exc


def public_pdf(session: requests.Session, url: str)->bytes:
    with session.get(url,headers={"Referer":LANDING,"Accept":"application/pdf"},
                     timeout=(15,60),allow_redirects=False,stream=True) as res:
        if res.status_code in (403,429):
            raise OfficialAnnualPdfUnavailable("NSE_ANNUAL_PDF_ACCESS_BLOCKED_"+str(res.status_code))
        if res.status_code!=200:
            raise OfficialAnnualPdfUnavailable("NSE_ANNUAL_PDF_HTTP_"+str(res.status_code))
        chunks=[]
        total=0
        for chunk in res.iter_content(65536):
            if not chunk:
                continue
            total+=len(chunk)
            if total>MAX_PDF_BYTES:
                raise OfficialAnnualPdfUnavailable("NSE_ANNUAL_PDF_TOO_LARGE")
            chunks.append(chunk)
    payload=b"".join(chunks)
    if (len(payload)<4000 or not payload.startswith(b"%PDF-")
        or b"%%EOF" not in payload[-2048:]):
        raise OfficialAnnualPdfUnavailable("NSE_ANNUAL_PDF_INVALID_OR_TRUNCATED")
    return payload



def baseline_coverage(root: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Use both existing local FY baselines to focus archival SOURCE intake.

    A six-field-filled baseline is a *research lead*, NEVER a verified
    audited annual result or financial/strategy PASS. Absence of the
    baseline cannot exclude any NSE EQ symbol from eventual discovery.
    """
    existing, core_complete = {}, {}
    for path in PRIORITY_SOURCE_FILES:
        present=set()
        complete=set()
        for row in read_rows(root/path):
            sym=str(row.get("Symbol","")).strip().upper()
            if not SYMBOL_PATTERN.fullmatch(sym):
                continue
            present.add(sym)
            try:
                values=[float(str(row.get(k,"")).replace(",","").strip())
                        for k in CORE_FUNDAMENTAL_FIELDS]
                if all(math.isfinite(x) for x in values):
                    complete.add(sym)
            except (ValueError,TypeError):
                pass
        for sym in present:
            existing[sym]=existing.get(sym,0)+1
        for sym in complete:
            core_complete[sym]=core_complete.get(sym,0)+1
    return existing, core_complete


def annual_source_priority(symbol:str, existing:dict[str,int],
                           core_complete:dict[str,int]) -> int:
    """0 is fastest credible four-year audit path, 3 is new/no FY baseline."""
    if core_complete.get(symbol,0)==2:
        return 0
    if existing.get(symbol,0)==2:
        return 1
    if existing.get(symbol,0)==1:
        return 2
    return 3


def choose_symbols(root:Path, now:datetime, limit:int)->list[dict]:
    verified=verified_discovered_symbols(root,now=now)
    catalog=read_rows(root/CATALOG)
    if len(catalog)!=len(verified):
        raise OfficialAnnualPdfUnavailable("VERIFIED_NSE_MAINBOARD_CATALOG_COUNT_CHANGED")
    existing,core_complete=baseline_coverage(root)
    previous={r["Symbol"]:r for r in read_rows(root/LEDGER) if r.get("Symbol")}
    candidates=[]
    for row in catalog:
        s=row.get("Symbol","")
        if s not in verified:
            raise OfficialAnnualPdfUnavailable("OFFICIAL_MAINBOARD_MEMBERSHIP_NOT_MATCHED")
        already=previous.get(s,{})
        candidates.append((already.get("Checked_At_UTC",""),
                           annual_source_priority(s,existing,core_complete),
                           row.get("Baseline_Tracked")!="false",s,row))
    # Never-checked sources go first, then oldest check. Within a tie,
    # companies with BOTH six-field annual FY baselines lead because their
    # older audited documents have the shortest route to a complete review.
    # New companies are still eventually checked and NOT excluded.
    candidates.sort(key=lambda x:(x[0],x[1],x[2],x[3]))
    return [x[4] for x in candidates[:limit]]


def acquire_pdfs(root:Path,candidates:list[dict],receipts:list[dict],
                 *,session,now:datetime,limit:int)->tuple[int,str]:
    if limit==0:
        return 0,"PDF_DOWNLOAD_DISABLED"
    existing={(r.get("Symbol"),r.get("Financial_Year"),
               r.get("Official_Report_URL")) for r in receipts}
    size=sum(path.stat().st_size for path in (root/PDF_DIR).glob("*.pdf")) if (root/PDF_DIR).is_dir() else 0
    added=0
    state="CANDIDATE_PDFS_ARCHIVED_NOT_FINANCIAL_VERIFIED"
    for item in candidates:
        if added>=limit:
            break
        if item["Financial_Year"] not in REQUIRED_YEARS:
            continue
        key=(item["Symbol"],item["Financial_Year"],item["Official_Report_URL"])
        if key in existing:
            continue
        url=item["Official_Report_URL"]
        if not source_pdf(url,item["Symbol"]):
            continue
        if size>=MAX_PDF_STORAGE_BYTES:
            state="PDF_STORAGE_BUDGET_REACHED"
            break
        if shutil.disk_usage(root).free<MIN_AVAILABLE_BYTES+MAX_PDF_BYTES:
            state="LOCAL_DISK_FREE_SPACE_TOO_LOW"
            break
        try:
            payload=public_pdf(session,url)
        except (OfficialAnnualPdfUnavailable,requests.RequestException) as exc:
            if "ACCESS_BLOCKED" in str(exc):
                state="NSE_PDF_DOWNLOAD_ACCESS_BLOCKED"
                break
            state="SOME_ANNUAL_PDFS_UNAVAILABLE"
            continue
        if size+len(payload)>MAX_PDF_STORAGE_BYTES:
            state="PDF_STORAGE_BUDGET_REACHED"
            break
        folder=root/PDF_DIR
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        name=hashlib.sha256(url.encode("utf-8")).hexdigest()[:28]+".pdf"
        path=folder/name
        digest=hashlib.sha256(payload).hexdigest()
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise OfficialAnnualPdfUnavailable(
                    "CACHED_PRIVATE_ANNUAL_REPORT_PDF_INTEGRITY_MISMATCH"
                )
        else:
            tmp=path.with_suffix(".pdf.tmp")
            try:
                tmp.write_bytes(payload)
                os.chmod(tmp,0o600)
                os.replace(tmp,path)
            finally:
                tmp.unlink(missing_ok=True)
        receipt={
            "Symbol":item["Symbol"],"Financial_Year":item["Financial_Year"],
            "Official_Report_URL":url,
            "Source_Index_SHA256":item["Original_Metadata_SHA256"],
            "PDF_SHA256":digest,"PDF_Bytes":len(payload),
            "Cached_Private_Path":str(path.relative_to(root)),
            "Downloaded_At_UTC":now.astimezone(timezone.utc).isoformat(),
            "Review_Status":"REQUIRES_AUDITED_CONSOLIDATED_FULL_YEAR_NUMERIC_RECONCILIATION",
        }
        receipts.append(receipt)
        existing.add(key)
        size+=len(payload)
        added+=1
    return added,state



def source_access_cooldown(root:Path, now:datetime)->str:
    """Return the original NSE source block's expiry without altering it.

    Applies to a real NSE annual-index/landing 403/429 from this collector;
    an ordinary empty PDF index or missing older year is not an access block.
    """
    path=root/STATUS
    if not path.is_file():
        return ""
    if path.is_symlink() or path.stat().st_size>50_000:
        raise OfficialAnnualPdfUnavailable("UNSAFE_ANNUAL_SOURCE_STATUS_FILE")
    try:
        report=json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise OfficialAnnualPdfUnavailable("ANNUAL_SOURCE_STATUS_UNREADABLE") from exc
    if not isinstance(report,dict):
        raise OfficialAnnualPdfUnavailable("ANNUAL_SOURCE_STATUS_SCHEMA_INVALID")
    state=report.get("state")
    if state=="NSE_OFFICIAL_ANNUAL_INDEX_ACCESS_BLOCKED":
        pass
    elif (state=="OFFICIAL_ANNUAL_INDEX_BLOCKED"
          and "ACCESS_BLOCKED" in str(report.get("reason",""))):
        pass
    else:
        return ""
    try:
        seen=datetime.fromisoformat(report["checked_at_utc"])
        if seen.tzinfo is None:
            raise ValueError("timezone missing")
        expiry=seen+ACCESS_COOLDOWN
        if seen<=now<expiry:
            return expiry.astimezone(timezone.utc).isoformat()
    except (KeyError,ValueError,TypeError,OverflowError) as exc:
        raise OfficialAnnualPdfUnavailable("ANNUAL_SOURCE_BLOCK_TIMESTAMP_INVALID") from exc
    return ""


def offline_pdf_source_plan(root:Path, now:datetime, limit:int)->dict:
    """Source queue only: no HTTP session, no PDF bytes, no mutations."""
    if not 1<=limit<=MAX_SYMBOLS:
        raise ValueError("OFFICIAL_ANNUAL_PDF_BOUNDED_LIMIT_REQUIRED")
    selected=choose_symbols(root,now,limit)
    original_index=read_rows(root/OUTPUT)
    original_receipts=read_rows(root/PDF_LOG)
    # Receipt is NOT source validation: only a locally saved metadata
    # claim. Original bytes/SHA, issuer, audited annual context and basis
    # are independently checked by the later PDF reviewer.
    index_keys={
        (r.get("Symbol"),r.get("Financial_Year"),r.get("Official_Report_URL"),
         r.get("Original_Metadata_SHA256"))
        for r in original_index
        if r.get("Financial_Year") in REQUIRED_YEARS
        and source_pdf(r.get("Official_Report_URL",""),r.get("Symbol",""))
    }
    receipt_keys={
        (r.get("Symbol"),r.get("Financial_Year"),
         r.get("Official_Report_URL"),r.get("Source_Index_SHA256"))
        for r in original_receipts
    }
    total_index_by_fy={
        fy:sum(r[1]==fy for r in index_keys)
        for fy in sorted(REQUIRED_YEARS)
    }
    unarchived=[r for r in index_keys if r not in receipt_keys]
    proposed=[]
    for entry in selected[:10]:
        symbol=entry["Symbol"]
        years_indexed={
            fy for (sym,fy,url,sha) in index_keys if sym==symbol
        }
        years_with_receipts={
            fy for (sym,fy,url,sha) in index_keys
            if sym==symbol and (sym,fy,url,sha) in receipt_keys
        }
        proposed.append({
            "Symbol":symbol,
            "Existing_FY_Index_Years":sorted(years_indexed),
            "Existing_FY_PDF_Receipt_Years_Not_Rehashed":sorted(years_with_receipts),
            "Next_Research_Action":(
                "OFFICIAL_NSE_PDF_SECTION_AND_NUMERIC_REVIEW"
                if len(years_with_receipts)==2
                else "DOWNLOAD_ALREADY_INDEXED_NSE_ANNUAL_PDF_IF_ACCESS_ALLOWED"
                if years_indexed-years_with_receipts
                else "CHECK_ORIGINAL_NSE_ANNUAL_REPORT_METADATA_IF_ACCESS_ALLOWED"
            ),
        })
    return {
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"OFFICIAL_NSE_ANNUAL_PDF_SOURCE_PLAN_ONLY_NO_HTTP",
        "selected_mainboard_symbols":len(selected),
        "indexed_original_nse_pdf_links_by_year":total_index_by_fy,
        "cached_source_receipts_not_rehashed":len(index_keys & receipt_keys),
        "indexed_source_links_without_receipt":len(unarchived),
        "previous_access_cooldown_until_utc":source_access_cooldown(root,now),
        "proposed_next_source_symbols":proposed,
        "annual_api_requests":0,"pdf_download_requests":0,
        "archived_pdf_bytes_read":0,
        "new_audited_fundamental_facts":0,
        "strict_fundamental_passes_added":0,
        "orders":"DISABLED",
    }



def offline_cached_index_intake(root:Path,*,downloads:int=0,
                                now:datetime|None=None,session=None)->dict:
    """Review previously indexed official NSE annual PDF links, no metadata API.

    downloads=0: NO network, HTTP session or saved filing/receipt mutations.
    downloads=1..2: only previously indexed NSE archive PDF URLs may be
    requested. Source PDF facts remain RESEARCH ONLY, never audited XBRL.
    """
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None or not 0<=downloads<=MAX_DOWNLOADS:
        raise ValueError("CACHED_OFFICIAL_PDF_LIMIT_OR_CLOCK_INVALID")
    verified=verified_discovered_symbols(root,now=now)
    catalog=read_rows(root/CATALOG)
    if (len(catalog)!=len(verified) or
        len({r.get("Symbol") for r in catalog})!=len(catalog) or
        any(r.get("Symbol") not in verified or
            r.get("Series")!="EQ" or not r.get("ISIN")
            for r in catalog)):
        raise OfficialAnnualPdfUnavailable("OFFICIAL_NSE_MAINBOARD_EQ_ISIN_NOT_ATTESTED")
    owners={r["Symbol"]:r for r in catalog}
    raw=read_rows(root/OUTPUT)
    if len(raw)>10000:
        raise OfficialAnnualPdfUnavailable("CACHED_ANNUAL_PDF_INDEX_TOO_LARGE")
    unique={}
    by_url={}
    for r in raw:
        sym=r.get("Symbol","")
        fy=r.get("Financial_Year","")
        url=r.get("Official_Report_URL","")
        if sym not in owners or r.get("ISIN")!=owners[sym]["ISIN"]:
            raise OfficialAnnualPdfUnavailable("CACHED_ANNUAL_PDF_ISSUER_ISIN_CHANGED")
        if (fy not in REQUIRED_YEARS or not source_pdf(url,sym) or
            r.get("Verification_State")!=
                "OFFICIAL_PDF_CANDIDATE_UNAUDITED_NUMERIC_FACTS" or
            not re.fullmatch(r"[0-9a-f]{64}",r.get("Original_Metadata_SHA256",""))):
            raise OfficialAnnualPdfUnavailable("CACHED_ANNUAL_PDF_INDEX_SOURCE_UNTRUSTED")
        try:
            from_year=int(r.get("From_Year",""))
            to_year=int(r.get("To_Year",""))
        except (ValueError,TypeError) as exc:
            raise OfficialAnnualPdfUnavailable("CACHED_ANNUAL_PDF_FY_INVALID") from exc
        if (to_year!=from_year+1 or
            fy!=f"{from_year}-{str(to_year)[-2:]}"):
            raise OfficialAnnualPdfUnavailable("CACHED_ANNUAL_PDF_FY_NOT_SOURCE_ATTESTED")
        key=(sym,fy,url)
        # One source URL must never approve multiple issuer-year records.
        if key in unique or (url in by_url and by_url[url]!=key):
            raise OfficialAnnualPdfUnavailable("DUPLICATE_OR_AMBIGUOUS_CACHED_NSE_PDF_URL")
        unique[key]=r
        by_url[url]=key
    receipts=read_rows(root/PDF_LOG)
    receipt_keys=set()
    if receipts:
        from nse_annual_pdf_section_review import checked_sources
        for receipt,entry,path in checked_sources(root,now):
            key=(receipt["Symbol"],receipt["Financial_Year"],
                 receipt["Official_Report_URL"])
            if key in receipt_keys or key not in unique:
                raise OfficialAnnualPdfUnavailable(
                    "CACHED_ANNUAL_PDF_RECEIPT_DUPLICATE_OR_UNINDEXED")
            receipt_keys.add(key)
    existing,complete=baseline_coverage(root)
    remaining=[row for key,row in unique.items() if key not in receipt_keys]
    remaining.sort(key=lambda r:(
        annual_source_priority(r["Symbol"],existing,complete),
        r["Financial_Year"],r["Symbol"],r["Official_Report_URL"]))
    stamp=now.astimezone(timezone.utc).isoformat()
    report={
        "checked_at_utc":stamp,
        "state":"OFFLINE_CACHED_NSE_ANNUAL_PDF_INDEX_REVIEW_COMPLETE",
        "official_nse_mainboard_eq_companies":len(owners),
        "previously_indexed_official_pdf_candidate_links":len(unique),
        "already_archived_original_pdf_receipts":len(receipt_keys),
        "validated_indexed_pdf_links_pending_archive":len(remaining),
        "two_newer_fy_six_field_priority_links_pending_archive":sum(
            annual_source_priority(r["Symbol"],existing,complete)==0
            for r in remaining),
        "next_indexed_pdf_candidates":[{
            "symbol":r["Symbol"],"financial_year":r["Financial_Year"]}
            for r in remaining[:8]],
        "annual_report_metadata_api_calls_this_run":0,
        "old_financial_results_api_calls_this_run":0,
        "pdf_archive_request_budget_this_run":downloads,
        "pdf_archive_requests_this_run":0 if downloads==0 else "UP_TO_BUDGET",
        "new_archived_pdf_candidates_this_run":0,
        "pdf_index_is_previous_metadata_not_reverified_xml":True,
        "pdf_candidate_financials_strict_verified":False,
        "new_strict_fundamental_passes":0,
        "orders":"DISABLED",
    }
    if not downloads or not remaining:
        if not remaining:
            report["state"]="CACHED_NSE_ANNUAL_PDF_INDEX_NO_PENDING_DOCUMENTS"
        atomic_status(root/CACHED_STATUS,report)
        return report
    own=session is None
    session=session or requests.Session()
    try:
        if own:
            session.headers.update({
                "User-Agent":"Mozilla/5.0",
                "Referer":LANDING,"Accept":"application/pdf",
            })
        added,state=acquire_pdfs(root,remaining,receipts,session=session,
                                now=now,limit=downloads)
        report["state"]="CACHED_NSE_ANNUAL_PDF_SOURCE_ARCHIVE_BOUNDED"
        report["new_archived_pdf_candidates_this_run"]=added
        report["pdf_archive_state"]=state
        report["validated_indexed_pdf_links_pending_archive"]-=added
        if added:
            write_csv_atomic(root/PDF_LOG,PDF_FIELDS,receipts)
    finally:
        if own:
            session.close()
    # Crucial: leave prior metadata API HTTP-403 cooldown in STATUS
    # untouched. A separate private status tracks this cached-only mode.
    atomic_status(root/CACHED_STATUS,report)
    return report


def collect(root:Path,*,limit:int=25,downloads:int=1,
            now:datetime|None=None,session=None,plan_only:bool=False)->dict:
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    if not 1<=limit<=MAX_SYMBOLS or not 0<=downloads<=MAX_DOWNLOADS:
        raise ValueError("OFFICIAL_ANNUAL_PDF_BOUNDED_LIMIT_REQUIRED")
    if plan_only:
        return offline_pdf_source_plan(root,now,limit)
    expiry=source_access_cooldown(root,now)
    if expiry:
        # Preserve ORIGINAL 403/429 timestamp in STATUS. Repeated
        # maintenance runs must not renew or evade the source cooldown.
        return {
            "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
            "state":"NSE_ANNUAL_SOURCE_ACCESS_COOLDOWN_NO_REQUEST",
            "next_eligible_at_utc":expiry,
            "company_symbols_checked_this_run":0,
            "annual_api_requests":0,"pdf_download_requests":0,
            "new_strict_fundamental_passes":0,"orders":"DISABLED",
        }
    root.mkdir(parents=True,exist_ok=True)
    chosen=choose_symbols(root,now,limit)
    all_candidates=read_rows(root/OUTPUT)
    lookup={(r.get("Symbol"),r.get("Financial_Year"),r.get("Official_Report_URL")):r
            for r in all_candidates}
    ledger={r.get("Symbol"):r for r in read_rows(root/LEDGER) if r.get("Symbol")}
    receipts=read_rows(root/PDF_LOG)
    own=session is None
    session=session or requests.Session()
    stamp=now.astimezone(timezone.utc).isoformat()
    checked=added=errors=0
    state="OFFICIAL_ANNUAL_REPORT_METADATA_CAPTURED"
    try:
        if own:
            session.headers.update({
                "User-Agent":"Mozilla/5.0",
                "Referer":LANDING,"Accept":"application/json,text/plain,*/*",
            })
            init=session.get(LANDING,timeout=20,allow_redirects=False)
            if init.status_code in (403,429):
                raise OfficialAnnualPdfUnavailable("NSE_ANNUAL_LANDING_ACCESS_BLOCKED")
        for item in chosen:
            sym=item["Symbol"]
            try:
                found=parse_metadata(query_metadata(session,sym),sym,item["ISIN"],
                                     item.get("Company",""),stamp)
            except (OfficialAnnualPdfUnavailable,requests.RequestException) as exc:
                if "BLOCKED" in str(exc):
                    state="NSE_OFFICIAL_ANNUAL_INDEX_ACCESS_BLOCKED"
                    break
                errors+=1
                ledger[sym]={"Symbol":sym,"Checked_At_UTC":stamp,
                             "Status":str(exc)[:120],"Matching_PDF_Count":0}
                checked+=1
                continue
            checked+=1
            ledger[sym]={"Symbol":sym,"Checked_At_UTC":stamp,
                         "Status":"OFFICIAL_METADATA_RECEIVED",
                         "Matching_PDF_Count":len(found)}
            for entry in found:
                key=(sym,entry["Financial_Year"],entry["Official_Report_URL"])
                if key not in lookup:
                    lookup[key]=entry
                    added+=1
        # Publish only validated metadata, with no implied audit of PDF facts.
        ordered=sorted(lookup.values(),key=lambda x:(
            x.get("Symbol",""),x.get("Financial_Year",""),x.get("Official_Report_URL","")))
        write_csv_atomic(root/OUTPUT,OUTPUT_FIELDS,ordered)
        write_csv_atomic(root/LEDGER,LEDGER_FIELDS,
                         sorted(ledger.values(),key=lambda x:x["Symbol"]))
        # Download only already indexed, official and source-bound PDF URLs.
        # Give best-coverage companies priority even if they are alphabetically
        # later; all candidates remain indexed independently of source rank.
        existing,core_complete=baseline_coverage(root)
        download_queue=sorted(ordered,key=lambda r:(
            annual_source_priority(r.get("Symbol",""),existing,core_complete),
            r.get("Financial_Year",""),r.get("Symbol",""),
            r.get("Official_Report_URL","")))
        selected_complete=sum(
            annual_source_priority(row["Symbol"],existing,core_complete)==0
            for row in chosen
        )
        downloaded=0
        pdf_state="SKIPPED_ON_ACCESS_BLOCK"
        if state!="NSE_OFFICIAL_ANNUAL_INDEX_ACCESS_BLOCKED":
            downloaded,pdf_state=acquire_pdfs(root,download_queue,receipts,session=session,
                                              now=now,limit=downloads)
            write_csv_atomic(root/PDF_LOG,PDF_FIELDS,receipts)
        report={
            "checked_at_utc":stamp,
            "state":state,
            "company_symbols_checked_this_run":checked,
            "new_validated_pdf_metadata_candidates":added,
            "total_candidate_pdf_index_rows":len(ordered),
            "indexed_company_total":len(ledger),
            "metadata_errors_this_run":errors,
            "prioritized_two_year_six_core_company_sources_selected":selected_complete,
            "intake_priority_is_not_audited_fundamental_verification":True,
            "new_archived_pdf_candidates_this_run":downloaded,
            "pdf_archive_state":pdf_state,
            "pdf_storage_limit_bytes":MAX_PDF_STORAGE_BYTES,
            "all_pdf_financial_facts_require_source_audit":True,
            "new_strict_fundamental_passes":0,
            "orders":"DISABLED",
        }
        atomic_status(root/STATUS,report)
        return report
    finally:
        if own:
            session.close()


def main()->int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit",type=int,default=25)
    ap.add_argument("--download",type=int,default=1)
    ap.add_argument("--plan-only",action="store_true",
                    help="List safe official NSE annual PDF source gaps without API calls or writes")
    ap.add_argument("--cached-index-only",action="store_true",
        help="Use only already indexed NSE archive PDF links; no metadata endpoint. With --download 0 there are no HTTP calls")
    args=ap.parse_args()
    try:
        if args.cached_index_only:
            if args.plan_only:
                raise ValueError("CACHED_INDEX_ONLY_USE_DOWNLOAD_ZERO_FOR_OFFLINE_PLAN")
            result=offline_cached_index_intake(
                data_root(),downloads=args.download)
        else:
            result=collect(data_root(),limit=args.limit,downloads=args.download,
                           plan_only=args.plan_only)
    except (OfficialAnnualPdfUnavailable,ValueError,OSError,
            requests.RequestException) as exc:
        result={"checked_at_utc":datetime.now(timezone.utc).isoformat(),
                "state":"OFFICIAL_ANNUAL_INDEX_BLOCKED","reason":str(exc)[:140],
                "previous_reports_preserved":True,"orders":"DISABLED"}
        atomic_status(data_root()/STATUS,result)
        print(json.dumps(result,indent=2))
        return 1
    print(json.dumps(result,indent=2))
    return 1 if result.get("state")=="NSE_OFFICIAL_ANNUAL_INDEX_ACCESS_BLOCKED" else 0


if __name__=="__main__":
    raise SystemExit(main())
