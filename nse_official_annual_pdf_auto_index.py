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
from datetime import datetime, timezone
import hashlib
import json
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


def choose_symbols(root:Path, now:datetime, limit:int)->list[dict]:
    verified=verified_discovered_symbols(root,now=now)
    catalog=read_rows(root/CATALOG)
    if len(catalog)!=len(verified):
        raise OfficialAnnualPdfUnavailable("VERIFIED_NSE_MAINBOARD_CATALOG_COUNT_CHANGED")
    previous={r["Symbol"]:r for r in read_rows(root/LEDGER) if r.get("Symbol")}
    candidates=[]
    for row in catalog:
        s=row.get("Symbol","")
        if s not in verified:
            raise OfficialAnnualPdfUnavailable("OFFICIAL_MAINBOARD_MEMBERSHIP_NOT_MATCHED")
        already=previous.get(s,{})
        candidates.append((row.get("Baseline_Tracked")!="false",
                           already.get("Checked_At_UTC",""),
                           s,row))
    # Untracked EQ companies always prioritized, but older checks rotate
    # predictably so no company gets permanent starvation.
    candidates.sort(key=lambda x:(x[0],x[1],x[2]))
    return [x[3] for x in candidates[:limit]]


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
        if not path.exists():
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


def collect(root:Path,*,limit:int=25,downloads:int=1,
            now:datetime|None=None,session=None)->dict:
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    if not 1<=limit<=MAX_SYMBOLS or not 0<=downloads<=MAX_DOWNLOADS:
        raise ValueError("OFFICIAL_ANNUAL_PDF_BOUNDED_LIMIT_REQUIRED")
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
        downloaded=0
        pdf_state="SKIPPED_ON_ACCESS_BLOCK"
        if state!="NSE_OFFICIAL_ANNUAL_INDEX_ACCESS_BLOCKED":
            downloaded,pdf_state=acquire_pdfs(root,ordered,receipts,session=session,
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
    args=ap.parse_args()
    try:
        result=collect(data_root(),limit=args.limit,downloads=args.download)
    except (OfficialAnnualPdfUnavailable,ValueError,OSError,
            requests.RequestException) as exc:
        result={"checked_at_utc":datetime.now(timezone.utc).isoformat(),
                "state":"OFFICIAL_ANNUAL_INDEX_BLOCKED","reason":str(exc)[:140],
                "previous_reports_preserved":True,"orders":"DISABLED"}
        atomic_status(data_root()/STATUS,result)
        print(json.dumps(result,indent=2))
        return 1
    print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
