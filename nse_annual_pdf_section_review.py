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

OUTPUT="nse_annual_pdf_section_review.csv"  # append-safe VERSIONED history view
LATEST_OUTPUT="nse_annual_pdf_section_review_latest.csv"  # current engine only
STATUS="runtime/nse_annual_pdf_section_review_status.json"
VERSION="section-review-5-split-consolidated-balance-sheet"
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
    """Coarse source samples, reserving most pages for a targeted dense scan."""
    if not 15<=count<=MAX_PAGES:
        raise SectionReviewBlocked("UNREASONABLE_PDF_PAGE_COUNT")
    sample=list(range(max(5,count//3),count,max(9,(count//3)//16)))
    # Keeping 24 or fewer sparse pages preserves the strictly bounded
    # 90-page parsing budget on long annual reports.
    stride=max(1,(len(sample)+23)//24)
    return sample[::stride][:24]


def dense_financial_band(count):
    """Likely late-report consolidated section, never proof of its presence.

    India annual reports commonly place consolidated statements after the
    standalone report. Inspect adjacent pages rather than hoping one of four
    consecutive statement pages exactly matches a sparse 9-page stride.
    """
    span=min(50,max(12,round(count*.18)))
    center=round(count*.715)
    start=max(4,min(count-span,center-span//2))
    return range(start,start+span)


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
    pnl=re.compile(r"consolidated\s+(?:statement\s+of\s+)?profit\s*(?:and|&)\s*loss(?:\s+statement)?",re.I)
    cf=re.compile(r"consolidated\s+(?:statement\s+of\s+)?cash\s+flow",re.I)

    def match(i):
        t=get_page(i)
        if not t:
            return False
        # A section index (e.g., a page of all four 'Consolidated ...'
        # headings) and an auditor's narrative list can mention ALL four
        # statement names. Actual STATEMENT pages must contain independent
        # table-row corroboration, not just a title. These are candidate
        # page locations ONLY, never audited numerical facts.
        auditor=(bool(auditor_heading.search(t))
                 and bool(re.search(r"\bwe\s+have\s+audited\b",t,re.I))
                 and bool(re.search(r"\b(?:basis\s+for\s+opinion|in\s+our\s+opinion)\b",t,re.I))
                 and bool(re.search(r"consolidated",t,re.I)))
        # Some original annual-report balance sheets label the final
        # assets total simply "TOTAL", not "TOTAL ASSETS". Such a page
        # needs independently present share capital, Equity/Liabilities
        # heading and a two-column amount row; an index alone cannot pass.
        balance=(bool(bs.search(t))
                 and bool(re.search(r"\bequity\s+and\s+liabilities\b",t,re.I))
                 and (bool(re.search(r"\btotal\s+assets\b",t,re.I))
                      or (bool(re.search(r"\bequity\s+share\s+capital\b",t,re.I))
                          and bool(re.search(
                              r"\btotal\s+\(?-?\d[\d,]*\.\d{2}\)?\s+\(?-?\d[\d,]*\.\d{2}\)?\b",
                              t,re.I)))))
        # Some NSE original PDFs print ASSETS and EQUITY/LIABILITIES
        # on two *adjacent* pages (e.g. 63MOONS FY2023-24). The first
        # page must be a consolidated balance sheet with both assets
        # subtotals and its two-year TOTAL ASSETS. The NEXT page must
        # explicitly be its CONSOLIDATED BALANCE SHEET (CONTD.), carry
        # equity/liability rows and have BOTH printed closing totals
        # equal to the first page. No numbers get promoted by this step.
        if not balance and bs.search(t) and i+1<count:
            amount=r"(\\(?-?\\d[\\d,]*\\.\\d{2}\\)?)"
            asset_pair=re.search(
                r"\\bTOTAL\\s+ASSETS\\s+"+amount+r"\\s+"+amount+r"\\b",t,re.I)
            has_asset_rows=(bool(re.search(r"\\bTOTAL\\s+NON[-\\s]*CURRENT\\s+ASSETS\\b",t,re.I))
                            and bool(re.search(r"\\bTOTAL\\s+CURRENT\\s+ASSETS\\b",t,re.I)))
            if asset_pair and has_asset_rows and str(2000+int(year[-2:])) in t:
                continuation=get_page(i+1)
                is_consolidated_continued=bool(re.search(
                    r"\\bCONSOLIDATED\\s+BALANCE\\s+SHEET\\s*\\(\\s*CONT(?:D\\.?|INUED)\\s*\\)",
                    continuation,re.I))
                closing=re.search(
                    r"\\bTOTAL\\s+EQUITY\\s+AND\\s+LIABILITIES\\s+"+
                    amount+r"\\s+"+amount+r"\\b",continuation,re.I)
                if (is_consolidated_continued
                    and str(2000+int(year[-2:])) in continuation
                    and re.search(r"\\bEQUITY\\s+AND\\s+LIABILITIES\\b",continuation,re.I)
                    and re.search(r"\\bEQUITY\\s+SHARE\\s+CAPITAL\\b",continuation,re.I)
                    and re.search(r"\\bTOTAL\\s+EQUITY\\b",continuation,re.I)
                    and closing
                    and tuple(x.replace(",","") for x in asset_pair.groups())==
                        tuple(x.replace(",","") for x in closing.groups())):
                    balance=True
        profits=(bool(pnl.search(t))
                 and bool(re.search(r"\brevenue\s+from\s+operations\b",t,re.I))
                 and bool(re.search(
                     r"\bprofit\s*(?:/\s*\(?loss\)?|\s*\(?loss\)?)?\s+"
                     r"(?:for\s+the\s+(?:period|year)|before\s+tax)\b",t,re.I)))
        cash=(bool(cf.search(t))
              and bool(re.search(r"\bcash\s+flow\s+from\s+operating\s+activities\b",t,re.I))
              and bool(re.search(r"\bcash\s+flow\s+from\s+investing\s+activities\b",t,re.I)))
        verdict={
            "auditor":auditor,
            "balance_sheet":balance,
            "profit_loss":profits,
            "cash_flow":cash,
        }
        for key,hit in verdict.items():
            if hit and (i+1) not in found[key]:
                found[key].append(i+1)
        return any(verdict.values())

    # Broad sampling discovers headings outside the usual financial pages.
    # A second dense late-report band rescues pages between sparse samples.
    # Reserve space for neighboring sections; annual consolidated auditor
    # opinions/annexures can run 10+ pages before the balance sheet.
    # Still a bounded PARTIAL page scan, not an audit of numeric facts.
    pivots=[]
    for i in seed:
        if len(scanned)>=MAX_SCAN_PAGES-12:
            break
        if match(i):
            pivots.append(i)
    for i in dense_financial_band(count):
        if len(scanned)>=MAX_SCAN_PAGES-12 or all(found.values()):
            break
        if match(i):
            pivots.append(i)
    # Prefer corroborated consolidated auditor pages: a long audit report
    # may put the three statements 8-18 physical pages *after* the opinion.
    # Do not widen the scan for generic document-title hits.
    ordered_pivots=sorted(set(pivots),key=lambda i:(i+1 not in found["auditor"],i))
    for i in ordered_pivots:
        if len(scanned)>=MAX_SCAN_PAGES or all(found.values()):
            break
        forward=19 if i+1 in found["auditor"] else 8
        for j in range(max(4,i-7),min(count,i+forward)):
            if len(scanned)>=MAX_SCAN_PAGES or all(found.values()):
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
            # Detect a suspicious one-page reference list: never report
            # four separate statements as distinct when all are on one page.
            pages_distinct=len(set(pages.values()))==4 if all(pages.values()) else False
            located=all(pages.values()) and pages_distinct
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

    # Keep immutable prior-version review evidence. Publishing a SECOND,
    # version-scoped current view prevents the archived v2 false-positive
    # for 20MICRONS (four sections at page 200) from being misread as the
    # active v3 result by 'head -n 2'. Never select an older row as current.
    current_rows=[r for r in rows if r.get("Review_Version")==VERSION]
    write_csv_atomic(root/LATEST_OUTPUT,FIELDS,current_rows)
    most_recent=sorted(
        current_rows,key=lambda r:(
            r.get("Reviewed_At_UTC",""),
            r.get("Symbol",""),r.get("Financial_Year",""),
        )
    )[-1] if current_rows else None
    last_reviewed={
        "symbol":most_recent.get("Symbol",""),
        "financial_year":most_recent.get("Financial_Year",""),
        "review_version":most_recent.get("Review_Version",""),
        "state":most_recent.get("Section_Review_State",""),
        "physical_pdf_pages":{
            "auditor":most_recent.get("Auditor_Page_1based",""),
            "balance_sheet":most_recent.get("Consolidated_Balance_Sheet_Page_1based",""),
            "profit_and_loss":most_recent.get("Consolidated_Profit_Loss_Page_1based",""),
            "cash_flow":most_recent.get("Consolidated_Cash_Flow_Page_1based",""),
        },
        "audited_financial_facts_verified":False,
    } if most_recent else None
    result={
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"PDF_SECTION_REVIEW_PARTIAL" if failures else "OFFICIAL_ARCHIVED_PDF_SECTION_REVIEW_UPDATED",
        "source_receipts_verified":len(verified),
        "reports_reviewed_this_run":processed,
        "candidate_reports_remaining":max(0,len(candidates)-processed),
        "previous_review_records":len(rows)-processed,
        "current_review_version":VERSION,
        "current_version_review_records":len(current_rows),
        "versioned_historical_reviews_preserved":True,
        "latest_review_csv":LATEST_OUTPUT,
        "most_recent_current_review":last_reviewed,
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
