#!/usr/bin/env python3
"""Bounded, unattended archival annual PDF SECTION review, NOT a financial parser.

Only archived NSE annual-report PDFs with an exact source-index and SHA256
receipt and a fresh NSE mainboard symbol+ISIN attestation are read. No PDF
numeric values are approved and no financial, backtest or trading data changes.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path
import re

from nse_mainboard_discovery import CATALOG,write_csv_atomic
from nse_official_annual_pdf_auto_index import (
    OUTPUT as PDF_INDEX,PDF_LOG,PDF_DIR,MAX_PDF_BYTES,
    source_pdf,
)
from research_monitor.discovered_annual_universe import verified_discovered_symbols
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

OUTPUT="nse_annual_pdf_section_review.csv"
STATUS="runtime/nse_annual_pdf_section_review_status.json"
VERSION="section-review-1"
MAX_FILES=2
MAX_SCAN_PAGES=90
MAX_PAGES=700
FIELDS=[
    "Symbol","ISIN","Financial_Year","Official_Report_URL",
    "PDF_SHA256","PDF_Pages","Pages_Scanned","Scan_Coverage",
    "Cover_Fiscal_Year_Candidate","Auditor_Page_1based",
    "Consolidated_Balance_Sheet_Page_1based",
    "Consolidated_Profit_Loss_Page_1based",
    "Consolidated_Cash_Flow_Page_1based","Section_Review_State",
    "Audited_Consolidated_Facts_Verified","Core_Financial_Facts_Verified",
    "Reviewed_At_UTC","Review_Version",
]


class SectionReviewBlocked(RuntimeError):
    pass


def records(path):
    if not path.is_file() or path.is_symlink():
        return []
    with path.open(encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f))


def checked_sources(root,now):
    checked=verified_discovered_symbols(root,now=now)
    catalog=records(root/CATALOG)
    if len(catalog)!=len(checked) or any(
        row.get("Symbol") not in checked or not row.get("ISIN")
        or row.get("Series")!="EQ" for row in catalog
    ):
        raise SectionReviewBlocked("OFFICIAL_CURRENT_MAINBOARD_EQ_NOT_ATTESTED")
    membership={r["Symbol"]:r["ISIN"] for r in catalog}
    index={
        (r.get("Symbol"),r.get("Financial_Year"),r.get("Official_Report_URL")):r
        for r in records(root/PDF_INDEX)
    }
    receipts=records(root/PDF_LOG)
    if not receipts:
        return []
    valid=[]
    for r in receipts:
        sym=r.get("Symbol","")
        year=r.get("Financial_Year","")
        url=r.get("Official_Report_URL","")
        key=(sym,year,url)
        entry=index.get(key)
        if (year not in ("2022-23","2023-24") or entry is None
            or entry.get("ISIN")!=membership.get(sym)
            or not source_pdf(url,sym)
            or entry.get("Original_Metadata_SHA256")!=r.get("Source_Index_SHA256")):
            raise SectionReviewBlocked("ANNUAL_PDF_SOURCE_IDENTITY_OR_INDEX_NOT_MATCHED")
        digest=r.get("PDF_SHA256","")
        if not re.fullmatch("[0-9a-f]{64}",digest):
            raise SectionReviewBlocked("ANNUAL_PDF_RECEIPT_DIGEST_INVALID")
        expected=PDF_DIR/(hashlib.sha256(url.encode("utf-8")).hexdigest()[:28]+".pdf")
        if r.get("Cached_Private_Path")!=expected.as_posix():
            raise SectionReviewBlocked("ANNUAL_PDF_RECEIPT_PATH_NOT_EXPECTED")
        pdf=root/expected
        if not pdf.is_file() or pdf.is_symlink():
            raise SectionReviewBlocked("ARCHIVED_SOURCE_PDF_MISSING_OR_SYMLINK")
        if not 4_000<=pdf.stat().st_size<=MAX_PDF_BYTES:
            raise SectionReviewBlocked("ARCHIVED_PDF_SIZE_INVALID")
        valid.append((r,entry,pdf))
    return valid


def page_indices(count):
    """Small coarse search + neighborhoods; report PARTIAL, never a full scan."""
    if not 15<=count<=MAX_PAGES:
        raise SectionReviewBlocked("UNREASONABLE_PDF_PAGE_COUNT")
    # Search first 4 pages for fiscal year and at most 10-page step 10
    # from one-third to the end. Then inspect +/-5 for candidate headings.
    sample=list(range(max(5,count//3),count,max(9,(count//3)//16)))
    return sample


def locate_sections(reader,year):
    count=len(reader.pages)
    seed=page_indices(count)
    scanned={}
    section_keys=(
        "auditor","balance_sheet","profit_loss","cash_flow",
    )
    found={k:[] for k in section_keys}

    def get_page(i):
        if i in scanned:
            return scanned[i]
        if len(scanned)>=MAX_SCAN_PAGES:
            return ""
        try:
            extracted=reader.pages[i].extract_text() or ""
        except (ValueError,KeyError,IndexError,TypeError,RuntimeError):
            extracted=""
        text=re.sub(r"\s+"," ",extracted).strip()
        scanned[i]=text
        return text

    cover=" ".join(get_page(i) for i in range(min(4,count)))
    token=re.sub("[^0-9]+"," ",cover)
    fiscal_year_seen=(year.replace("-"," ") in token)
    auditor_heading=re.compile(
        r"independent\s+auditors?(?:['\u2019]s?)?\s+report",re.I
    )
    bs=re.compile(r"consolidated\s+balance\s+sheet",re.I)
    pnl=re.compile(r"consolidated\s+(?:statement\s+of\s+)?profit\s+and\s+loss",re.I)
    cf=re.compile(r"consolidated\s+(?:statement\s+of\s+)?cash\s+flow",re.I)

    def match(i):
        t=get_page(i)
        if not t:
            return False
        is_consolidated=bool(re.search("consolidated",t,re.I))
        verdict={
            "auditor": is_consolidated and bool(
                auditor_heading.search(t)
                or re.search(r"\bopinion\s+we\s+have\s+audited\b",t,re.I)
            ),
            "balance_sheet": bool(bs.search(t)),
            "profit_loss": bool(pnl.search(t)),
            "cash_flow": bool(cf.search(t)),
        }
        for key,hit in verdict.items():
            if hit and (i+1) not in found[key]:
                found[key].append(i+1)
        return any(verdict.values())

    # Broad samples find a likely section; nearby pages reveal adjacent
    # individual statements. Source dates, amounts and audit basis are NOT
    # independently verified by this textual locator.
    pivots=[]
    for i in seed:
        if len(scanned)>=MAX_SCAN_PAGES-12:
            break
        if match(i):
            pivots.append(i)
    for i in pivots[:5]:
        if len(scanned)>=MAX_SCAN_PAGES:
            break
        for j in range(max(4,i-7),min(count,i+8)):
            if len(scanned)>=MAX_SCAN_PAGES:
                break
            match(j)
    for item in found.values():
        item.sort()
    return {
        "pdf_pages":count,"pages_scanned":len(scanned),
        "scan_coverage":"BOUNDED_PARTIAL_TEXT_ONLY",
        "cover_fiscal_year_candidate":fiscal_year_seen,
        "sections":{k:(v[0] if v else None) for k,v in found.items()},
        "audit_and_numeric_facts_verified":False,
    }


def inspect_pdf(path,year,reader_cls=None):
    if reader_cls is None:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise SectionReviewBlocked("PYPDF_REQUIRED_PIP_INSTALL_PYPDF") from exc
        reader_cls=PdfReader
    data=path.read_bytes()
    if (len(data)<4_000 or len(data)>MAX_PDF_BYTES
        or not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]):
        raise SectionReviewBlocked("INVALID_OR_TRUNCATED_ARCHIVED_PDF")
    try:
        reader=reader_cls(io.BytesIO(data),strict=True)
        if getattr(reader,"is_encrypted",False):
            raise SectionReviewBlocked("ENCRYPTED_PDF_NOT_SUPPORTED")
        return locate_sections(reader,year)
    except SectionReviewBlocked:
        raise
    except (ValueError,TypeError,KeyError,IndexError,RuntimeError) as exc:
        raise SectionReviewBlocked("PDF_TEXT_REVIEW_PARSE_FAILED") from exc


def run(root,max_files=MAX_FILES,now=None,reader_cls=None):
    if not 1<=int(max_files)<=MAX_FILES:
        raise ValueError("BOUNDED_MAX_FILES_1_OR_2_REQUIRED")
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    verified=checked_sources(root,now)
    previous=records(root/OUTPUT)
    prior={(r.get("Symbol"),r.get("Financial_Year"),
           r.get("Official_Report_URL"),r.get("PDF_SHA256"),
           r.get("Review_Version")):r for r in previous}
    candidates=[]
    for receipt,entry,pdf in verified:
        key=(receipt["Symbol"],receipt["Financial_Year"],
             receipt["Official_Report_URL"],receipt["PDF_SHA256"],VERSION)
        if key not in prior:
            candidates.append((receipt,entry,pdf,key))
    processed=0
    failures=0
    errors=[]
    for receipt,entry,pdf,key in candidates[:max_files]:
        # Read the actual bytes at review time, verify immutable source identity.
        # A bad SHA or changed receipt halts the batch; no facts are promoted.
        if hashlib.sha256(pdf.read_bytes()).hexdigest()!=receipt["PDF_SHA256"]:
            raise SectionReviewBlocked("CACHED_ARCHIVED_PDF_SHA256_CHANGED")
        try:
            details=inspect_pdf(pdf,receipt["Financial_Year"],reader_cls=reader_cls)
            pages=details["sections"]
            located=all(pages.values())
            state=("CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED"
                   if located and details["cover_fiscal_year_candidate"]
                   else "INCOMPLETE_SECTIONS_OR_FISCAL_YEAR_REQUIRES_REVIEW")
            row={
                "Symbol":receipt["Symbol"],"ISIN":entry["ISIN"],
                "Financial_Year":receipt["Financial_Year"],
                "Official_Report_URL":receipt["Official_Report_URL"],
                "PDF_SHA256":receipt["PDF_SHA256"],
                "PDF_Pages":details["pdf_pages"],
                "Pages_Scanned":details["pages_scanned"],
                "Scan_Coverage":details["scan_coverage"],
                "Cover_Fiscal_Year_Candidate":str(details["cover_fiscal_year_candidate"]).lower(),
                "Auditor_Page_1based":pages["auditor"] or "",
                "Consolidated_Balance_Sheet_Page_1based":pages["balance_sheet"] or "",
                "Consolidated_Profit_Loss_Page_1based":pages["profit_loss"] or "",
                "Consolidated_Cash_Flow_Page_1based":pages["cash_flow"] or "",
                "Section_Review_State":state,
                "Audited_Consolidated_Facts_Verified":"false",
                "Core_Financial_Facts_Verified":0,
                "Reviewed_At_UTC":now.astimezone(timezone.utc).isoformat(),
                "Review_Version":VERSION,
            }
        except SectionReviewBlocked as exc:
            failures+=1
            errors.append({"symbol":receipt["Symbol"],"reason":str(exc)[:100]})
            row={
                "Symbol":receipt["Symbol"],"ISIN":entry["ISIN"],
                "Financial_Year":receipt["Financial_Year"],
                "Official_Report_URL":receipt["Official_Report_URL"],
                "PDF_SHA256":receipt["PDF_SHA256"],
                "Section_Review_State":"PDF_REVIEW_BLOCKED_"+str(exc)[:80],
                "Audited_Consolidated_Facts_Verified":"false",
                "Core_Financial_Facts_Verified":0,
                "Reviewed_At_UTC":now.astimezone(timezone.utc).isoformat(),
                "Review_Version":VERSION,
            }
        prior[key]=row
        processed+=1
    # Preserve reports from earlier collectors; sort deterministic current
    # immutable source receipts without confusing old hash with current PDFs.
    rows=sorted(prior.values(),key=lambda r:(
        r.get("Symbol",""),r.get("Financial_Year",""),r.get("PDF_SHA256","")))
    if rows:
        write_csv_atomic(root/OUTPUT,FIELDS,rows)
    result={
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"PDF_SECTION_REVIEW_PARTIAL" if failures else "OFFICIAL_ARCHIVED_PDF_SECTION_REVIEW_UPDATED",
        "source_receipts_verified":len(verified),
        "reports_reviewed_this_run":processed,
        "candidate_reports_remaining":max(0,len(candidates)-processed),
        "previous_review_records":len(rows)-processed,
        "reports_blocked_this_run":failures,
        "errors":errors,
        "source_text_scans_are_partial":True,
        "year_and_audit_basis_require_independent_review":True,
        "financial_facts_added":0,
        "strict_annual_passes_added":0,
        "trading_enabled":False,"orders":"DISABLED",
    }
    atomic_status(root/STATUS,result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-files",type=int,default=MAX_FILES)
    args=p.parse_args()
    try:
        report=run(data_root(),max_files=args.max_files)
    except (SectionReviewBlocked,OSError,ValueError,csv.Error) as exc:
        report={
            "checked_at_utc":datetime.now(timezone.utc).isoformat(),
            "state":"ARCHIVED_PDF_SECTION_REVIEW_BLOCKED",
            "reason":str(exc)[:140],
            "verified_annual_data_preserved":True,
            "trading_enabled":False,"orders":"DISABLED",
        }
        atomic_status(data_root()/STATUS,report)
        print(json.dumps(report,indent=2))
        return 1
    print(json.dumps(report,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
