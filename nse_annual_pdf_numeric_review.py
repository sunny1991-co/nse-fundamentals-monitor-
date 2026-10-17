#!/usr/bin/env python3
"""Bounded, original-NSE-PDF numeric EVIDENCE CANDIDATES only.

This script intentionally cannot approve audited financials. It reads only
pre-downloaded, hash-attested annual PDFs and the verified-current-version PDF
page index. Six metrics are extracted as REVIEW CANDIDATES, not promoted to
annual_historical_results, strict screen, trading, or any strategy.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime,timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re

from nse_annual_pdf_section_review import (
    checked_sources, records, SectionReviewBlocked, MAX_PAGES,
    LATEST_OUTPUT, VERSION as SECTION_VERSION,
)
from nse_official_annual_pdf_auto_index import MAX_PDF_BYTES
from nse_mainboard_discovery import write_csv_atomic
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

OUTPUT="nse_annual_pdf_numeric_evidence_candidates.csv"
STATUS="runtime/nse_annual_pdf_numeric_evidence_status.json"
VERSION="numeric-evidence-candidate-v2-wrapped-layout"
MAX_FILES=2
LABELS={
    "Revenue_Operations_Cr":("profit_loss",re.compile(r"\brevenue\s+from\s+operations\b",re.I)),
    "Net_Profit_Cr":("profit_loss",re.compile(r"\bprofit\s+for\s+the\s+period\b",re.I)),
    "Basic_EPS_Rs":("profit_loss",re.compile(r"^\s*basic\b",re.I)),
    "Total_Assets_Cr":("balance_sheet",re.compile(r"^\s*total\s+assets\b",re.I)),
    "Total_Equity_Cr":("balance_sheet",re.compile(r"^\s*total\s+equity(?!\s+and\s+liabilities)\b",re.I)),
}
MORE={
    "NCI":re.compile(r"^\s*(?:\d+\s+)?non[-\s]*controlling\s+interest\b",re.I),
    "Liabilities":re.compile(r"^\s*total\s+liabilities\b",re.I),
    "Equity_And_Liabilities":re.compile(r"^\s*total\s+equity\s+and\s+liabilities\b",re.I),
    "Non_Current_Assets":re.compile(r"^\s*total\s+non[-\s]*current\s+assets\b",re.I),
    "Current_Assets":re.compile(r"^\s*total\s+current\s+assets\b",re.I),
    "Tax":re.compile(r"^\s*total\s+tax\s+expenses?\b",re.I),
    "Profit_Before_Tax":re.compile(r"^\s*(?:[IVX]+\.\s*)?profit\s+before\s+tax\s*\(VII\s*[-–]\s*VIII\)",re.I),
}
# Parse *only* two decimal columns on the SAME printed source row.
# A standalone list of titles, a narrative, or ambiguous wrapped text
# cannot yield approved numeric candidate cells.
PAIR=re.compile(r"(?<![\w,])(\(?-?\d[\d,]*\.\d{2}\)?)(?![\w])")
ROW_FIELDS=[
    "Symbol","ISIN","Financial_Year","Official_Report_URL","PDF_SHA256",
    "Section_Review_Version","Numeric_Review_Version",
    "Statement_Units","Fiscal_Year_End","Current_Column_Label",
    "Prior_Column_Label","Review_State","Candidate_Core_Fields",
    "Revenue_Operations_Cr","Net_Profit_Cr","Basic_EPS_Rs",
    "Total_Assets_Cr","Total_Equity_Cr","Total_Borrowings_Cr",
    "Noncurrent_Borrowings_Cr","Current_Borrowings_Cr",
    "Noncontrolling_Interest_Cr",
    "Balance_Sheet_Check","Profit_Tax_Check","Borrowings_Check",
    "Evidence_Pages_JSON","Evidence_Rows_JSON","Review_Issues_JSON",
    "Audited_Consolidated_Facts_Verified","Strict_Fundamental_Pass",
    "Orders","Reviewed_At_UTC",
]
SECTION_COLUMNS={
    "balance_sheet":"Consolidated_Balance_Sheet_Page_1based",
    "profit_loss":"Consolidated_Profit_Loss_Page_1based",
    "cash_flow":"Consolidated_Cash_Flow_Page_1based",
    "auditor":"Auditor_Page_1based",
}


def number(raw:str)->Decimal:
    value=raw.strip().replace(",","")
    if value.startswith("(") and value.endswith(")"):
        value="-"+value[1:-1]
    amount=Decimal(value)
    if not amount.is_finite():
        raise ValueError("NONFINITE_PDF_AMOUNT")
    return amount


# Only immediate numeric-only PDF continuation lines may be joined.
# Never bridge a second named row/heading or several unrelated cells.
NUMERIC_CONTINUATION=re.compile(
    r"^\s*(?:(?:\(?-?\d[\d,]*\.\d{2}\)?|"
    r"\(\s*-?\d[\d,]*\.\d{2}\s*\)|"
    r"(?:(?:Note|No\.?)\s+)?\d{1,3})\s*){1,3}$",re.I
)


def values_on_source_row(text:str,pattern:re.Pattern)->list[tuple[Decimal,str]]:
    """Unique 2-column printed rows; at most two immediate numeric-only wraps.

    Keep source line evidence and never join the next *named* row, table
    heading or a paragraph. Ambiguity fails closed in singular().
    """
    lines=[re.sub(r"\s+"," ",line.strip()) for line in text.splitlines()]
    found=[]
    for i,line in enumerate(lines):
        hit=pattern.search(line)
        if not hit:
            continue
        # A line's unrelated prior figures or note numbers are excluded.
        base=line[hit.start():]
        after=base[hit.end()-hit.start():]
        pair=list(PAIR.finditer(after))
        source=base
        if len(pair)<2:
            # pypdf default mode sometimes prints columns on the NEXT
            # physical text lines. Join only 1-2 immediate number-only
            # continuation lines, never through another financial label.
            for next_line in lines[i+1:i+3]:
                if not NUMERIC_CONTINUATION.fullmatch(next_line):
                    break
                source=source+" | "+next_line
                pair=list(PAIR.finditer(source[hit.end()-hit.start():]))
                if len(pair)>=2:
                    break
        if len(pair)!=2:
            continue
        # Extra decimals in the printed label's tail invalidate the pair.
        try:
            amounts=[number(m.group(1)) for m in pair]
        except (InvalidOperation,ValueError):
            continue
        found.append((amounts[0],source[:360]))
    return found


def singular(text:str,pattern:re.Pattern)->tuple[Decimal,str]|None:
    values=values_on_source_row(text,pattern)
    return values[0] if len(values)==1 else None


def across_layouts(text:str,layout:str,pattern:re.Pattern):
    """Never choose between conflicting PDF text extraction modes."""
    primary=singular(text,pattern)
    secondary=singular(layout,pattern) if layout else None
    if primary is not None and secondary is not None:
        if primary[0]!=secondary[0]:
            return None,"CONFLICTING_PDF_TEXT_EXTRACTION_MODES"
        return primary,"DEFAULT_AND_LAYOUT_AGREE"
    if primary:
        return primary,"DEFAULT_TEXT"
    if secondary:
        return secondary,"LAYOUT_TEXT"
    return None,"MISSING_OR_AMBIGUOUS_TWO_COLUMN_ROW"


def debt_rows(text:str,layout:str):
    """Find noncurrent/current borrowing rows only in correct BS sections.

    The section headings must be present and the current section must
    occur AFTER non-current. Lease liabilities are never included.
    """
    pat=re.compile(r"^\s*(?:\([ivx]+\)\s*)?borrowings\b",re.I)
    nc=re.compile(r"^\s*(?:\d+\s+)?non[-\s]*current\s+liabilities\b",re.I)
    cu=re.compile(r"^\s*(?:\d+\s+)?current\s+liabilities\b",re.I)

    def one_view(source):
        section=None
        grouped={"noncurrent":[],"current":[]}
        lines=source.splitlines()
        for i,line in enumerate(lines):
            normalized=re.sub(r"\s+"," ",line.strip())
            if nc.match(normalized):
                section="noncurrent"
            elif cu.match(normalized):
                section="current"
            if section is None or not pat.match(normalized):
                continue
            # Use this ONE label's local source line+number fragments only.
            block="\n".join(lines[i:i+3])
            candidate=values_on_source_row(block,pat)
            if len(candidate)==1:
                grouped[section].append(candidate[0])
        if any(len(grouped[k])!=1 for k in grouped):
            return None
        return [grouped["noncurrent"][0],grouped["current"][0]]

    main=one_view(text)
    alt=one_view(layout) if layout else None
    if main and alt:
        if [x[0] for x in main]!=[x[0] for x in alt]:
            return None,"CONFLICTING_PDF_TEXT_EXTRACTION_MODES"
        return main,"DEFAULT_AND_LAYOUT_AGREE"
    if main:
        return main,"DEFAULT_TEXT"
    if alt:
        return alt,"LAYOUT_TEXT"
    return None,"MISSING_OR_AMBIGUOUS_SOURCE_ROWS"

def crore(v:Decimal)->str:
    return str((v/Decimal(100)).quantize(Decimal("0.0001")))


def close(left:Decimal,right:Decimal,tolerance=Decimal("0.03"))->bool:
    return abs(left-right)<=tolerance


def financial_page_evidence(payload:bytes,review:dict,reader_cls=None)->dict:
    """Parse only independently located statement pages from same source PDF."""
    if (not 4000<=len(payload)<=MAX_PDF_BYTES or
        not payload.startswith(b"%PDF-") or b"%%EOF" not in payload[-2048:]):
        raise SectionReviewBlocked("INVALID_PDF_PAYLOAD_FOR_NUMERIC_REVIEW")
    if reader_cls is None:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise SectionReviewBlocked("PYPDF_REQUIRED") from exc
        reader_cls=PdfReader
    try:
        reader=reader_cls(io.BytesIO(payload),strict=True)
        if getattr(reader,"is_encrypted",False):
            raise SectionReviewBlocked("ENCRYPTED_PDF")
        total=len(reader.pages)
        if not 15<=total<=MAX_PAGES:
            raise SectionReviewBlocked("IMPLAUSIBLE_SOURCE_PAGE_COUNT")
        pages={}
        for name,col in SECTION_COLUMNS.items():
            ix=int(review[col])-1
            if not 0<=ix<total:
                raise SectionReviewBlocked("INVALID_REVIEWED_PHYSICAL_PDF_PAGE")
            pages[name]=ix
        if len(set(pages.values()))!=4:
            raise SectionReviewBlocked("DUPLICATE_SECTION_INDEX_PAGE")
        original_text={}
        layout_text={}
        for name,index in pages.items():
            original_text[name]=reader.pages[index].extract_text() or ""
            # Layout mode is optional across pypdf versions and fake readers.
            # No second source/network, no OCR and no more PDF pages.
            try:
                layout_text[name]=reader.pages[index].extract_text(
                    extraction_mode="layout") or ""
            except (TypeError,ValueError,NotImplementedError):
                layout_text[name]=""
    except SectionReviewBlocked:
        raise
    except (OSError,IndexError,TypeError,ValueError,KeyError,RuntimeError) as exc:
        raise SectionReviewBlocked("PDF_NUMERIC_SOURCE_TEXT_PARSE_FAILED") from exc

    fy=review["Financial_Year"]
    year_end=fy.split("-")[-1]
    fiscal_year_end="20"+year_end+"-03-31"
    issue=[]
    candidate={}
    evidence={}
    for field,(page,pattern) in LABELS.items():
        v,how=across_layouts(original_text[page],layout_text[page],pattern)
        if v is None:
            issue.append(field+":"+how)
            continue
        candidate[field]=v[0]
        evidence[field]={"page_1based":pages[page]+1,"raw_source_row":v[1],
                         "current_lakhs_or_eps":str(v[0]),
                         "pdf_extraction_mode":how}

    bs=original_text["balance_sheet"]
    profit=original_text["profit_loss"]
    # Source statement pages MUST explicitly declare both years and monetary
    # units. The page-index review does not attest columns or lakh/crore scale.
    units_ok=all(bool(re.search(r"\bin\s+lakhs\b",original_text[p],re.I))
                 for p in ("balance_sheet","profit_loss"))
    current_date=re.compile(
        r"\b31\s*(?:st)?\s*march[,\s]*20"+re.escape(year_end)+r"\b",re.I)
    previous_year=str(int("20"+year_end)-1)
    previous_date=re.compile(
        r"\b31\s*(?:st)?\s*march[,\s]*"+previous_year+r"\b",re.I)
    def expected_columns(text):
        current=current_date.search(text)
        previous=previous_date.search(text)
        return bool(current and previous and current.start()<previous.start())
    now_ok=all(expected_columns(original_text[p])
               for p in ("balance_sheet","profit_loss"))
    if not units_ok:
        issue.append("REQUIRED_LAKH_UNIT_MARKER_NOT_ON_BOTH_STATEMENT_PAGES")
    if not now_ok:
        issue.append("REQUIRED_FISCAL_YEAR_NOT_ON_BOTH_STATEMENT_PAGES")
    aux={}
    for name,pat in MORE.items():
        page="profit_loss" if name in ("Tax","Profit_Before_Tax") else "balance_sheet"
        v,how=across_layouts(original_text[page],layout_text[page],pat)
        if v:
            aux[name]=v[0]
            evidence[name]={"page_1based":pages[page]+1,
                            "raw_source_row":v[1],"current_lakhs":str(v[0]),
                            "pdf_extraction_mode":how}
        elif how=="CONFLICTING_PDF_TEXT_EXTRACTION_MODES":
            issue.append(name+":"+how)

    # Borrowings: TWO source rows only. Non-current must precede current.
    # This is financial borrowing debt and intentionally excludes leases.
    borrowings,borrow_mode=debt_rows(bs,layout_text["balance_sheet"])
    if borrowings is not None:
        candidate["Noncurrent_Borrowings_Cr"]=borrowings[0][0]
        candidate["Current_Borrowings_Cr"]=borrowings[1][0]
        candidate["Total_Borrowings_Cr"]=borrowings[0][0]+borrowings[1][0]
        evidence["Borrowings_Noncurrent"]={
            "page_1based":pages["balance_sheet"]+1,
            "raw_source_row":borrowings[0][1],
            "current_lakhs":str(borrowings[0][0]),
            "pdf_extraction_mode":borrow_mode,
        }
        evidence["Borrowings_Current"]={
            "page_1based":pages["balance_sheet"]+1,
            "raw_source_row":borrowings[1][1],
            "current_lakhs":str(borrowings[1][0]),
            "pdf_extraction_mode":borrow_mode,
        }
        borrow_check="BOTH_ROW_CANDIDATES_FOUND_UNVERIFIED"
    else:
        borrow_check="MISSING_OR_AMBIGUOUS_SOURCE_ROWS"
        issue.append("BORROWINGS_REQUIRES_TWO_INDEPENDENT_SOURCE_ROWS")
        if borrow_mode=="CONFLICTING_PDF_TEXT_EXTRACTION_MODES":
            issue.append("BORROWINGS_TEXT_MODES_CONFLICT")
    # Balance arithmetic is ONLY a reasonableness check. It does not verify
    # audited scope, reporting-basis, original XBRL, or debt completeness.
    asset=candidate.get("Total_Assets_Cr")
    eq=candidate.get("Total_Equity_Cr")
    if (asset is not None and eq is not None and
        all(k in aux for k in ("NCI","Liabilities","Equity_And_Liabilities",
                             "Non_Current_Assets","Current_Assets"))):
        checks=[
            close(asset,aux["Equity_And_Liabilities"]),
            close(asset,aux["Non_Current_Assets"]+aux["Current_Assets"]),
            close(asset,eq+aux["NCI"]+aux["Liabilities"]),
        ]
        balance_check="SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY" if all(checks) else "ARITHMETIC_CONFLICT"
        if not all(checks):
            issue.append("SOURCE_BALANCE_SHEET_ARITHMETIC_CONFLICT")
    else:
        balance_check="INSUFFICIENT_SOURCE_ROWS_FOR_RECONCILIATION"
        issue.append("SOURCE_BALANCE_SHEET_CHECK_INCOMPLETE")
    net=candidate.get("Net_Profit_Cr")
    if net is not None and all(k in aux for k in ("Tax","Profit_Before_Tax")):
        profit_check=("SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY"
                      if close(aux["Profit_Before_Tax"]-aux["Tax"],net)
                      else "ARITHMETIC_CONFLICT")
    else:
        profit_check="INSUFFICIENT_SOURCE_ROWS_FOR_RECONCILIATION"
    # If units/date/any arithmetic are wrong, publish source evidence
    # but no numeric candidate values, never a fake source verification.
    safe=(units_ok and now_ok and
          balance_check=="SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY" and
          profit_check=="SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY")
    raw_candidates={}
    if safe:
        for k,v in candidate.items():
            raw_candidates[k]=str(v.quantize(Decimal("0.01"))) if k=="Basic_EPS_Rs" else crore(v)
        if "NCI" in aux:
            raw_candidates["Noncontrolling_Interest_Cr"]=crore(aux["NCI"])
    state=("COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED"
           if safe and all(k in raw_candidates for k in (
               *LABELS,"Total_Borrowings_Cr"))
           else "INCOMPLETE_OR_AMBIGUOUS_SOURCE_NUMBERS_REVIEW_REQUIRED")
    if state!="COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED":
        raw_candidates={}
    return {
        "metrics":raw_candidates,
        "raw_evidence":evidence,
        "issues":issue,
        "state":state,
        "pages":{k:v+1 for k,v in pages.items()},
        "units":"INR_LAKHS" if units_ok else "UNCONFIRMED",
        "fy_end":fiscal_year_end,
        "balance_check":balance_check,"profit_check":profit_check,
        "borrowings_check":borrow_check,
    }


def run(root,*,max_files=1,now=None,reader_cls=None)->dict:
    if not 1<=max_files<=MAX_FILES:
        raise ValueError("BOUNDED_MAX_FILES_1_OR_2")
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    sources=checked_sources(root,now)
    reviewed={
        (r.get("Symbol"),r.get("Financial_Year"),r.get("Official_Report_URL"),
         r.get("PDF_SHA256")):r
        for r in records(root/LATEST_OUTPUT)
        if r.get("Review_Version")==SECTION_VERSION
    }
    previous=records(root/OUTPUT)
    existing={(r.get("Symbol"),r.get("Financial_Year"),r.get("PDF_SHA256"),
              r.get("Numeric_Review_Version")):r for r in previous}
    pending=[]
    blocked=[]
    for receipt,entry,path in sources:
        key=(receipt["Symbol"],receipt["Financial_Year"],receipt["PDF_SHA256"],VERSION)
        if key in existing:
            continue
        check=(receipt["Symbol"],receipt["Financial_Year"],
               receipt["Official_Report_URL"],receipt["PDF_SHA256"])
        view=reviewed.get(check)
        if not view or view.get("Section_Review_State")!=(
                "CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED"):
            blocked.append({"symbol":receipt["Symbol"],"reason":"CURRENT_SECTION_EVIDENCE_NOT_READY"})
            continue
        pending.append((receipt,entry,path,view,key))
    done=0
    complete=0
    for receipt,entry,path,view,key in pending[:max_files]:
        # Do not trust cached review alone; rehash private original PDF bytes
        # at numeric extraction time. Any mismatch aborts, preserving data.
        payload=path.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=receipt["PDF_SHA256"]:
            raise SectionReviewBlocked("ARCHIVED_PDF_BYTES_CHANGED_AFTER_SECTION_REVIEW")
        details=financial_page_evidence(payload,view,reader_cls=reader_cls)
        row={
            "Symbol":receipt["Symbol"],"ISIN":entry["ISIN"],
            "Financial_Year":receipt["Financial_Year"],
            "Official_Report_URL":receipt["Official_Report_URL"],
            "PDF_SHA256":receipt["PDF_SHA256"],
            "Section_Review_Version":SECTION_VERSION,
            "Numeric_Review_Version":VERSION,
            "Statement_Units":details["units"],
            "Fiscal_Year_End":details["fy_end"],
            "Current_Column_Label":"31 March "+details["fy_end"][:4],
            "Prior_Column_Label":"31 March "+str(int(details["fy_end"][:4])-1),
            "Review_State":details["state"],
            "Candidate_Core_Fields":sum(k in details["metrics"] for k in (
                *LABELS,"Total_Borrowings_Cr")),
            "Balance_Sheet_Check":details["balance_check"],
            "Profit_Tax_Check":details["profit_check"],
            "Borrowings_Check":details["borrowings_check"],
            "Evidence_Pages_JSON":json.dumps(details["pages"],sort_keys=True),
            "Evidence_Rows_JSON":json.dumps(details["raw_evidence"],sort_keys=True),
            "Review_Issues_JSON":json.dumps(details["issues"]),
            "Audited_Consolidated_Facts_Verified":"false",
            "Strict_Fundamental_Pass":"false",
            "Orders":"DISABLED",
            "Reviewed_At_UTC":now.astimezone(timezone.utc).isoformat(),
            **details["metrics"],
        }
        existing[key]=row
        done+=1
        complete+=details["state"]=="COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED"
    rows=sorted(existing.values(),key=lambda r:(
        r.get("Symbol",""),r.get("Financial_Year",""),
        r.get("PDF_SHA256",""),r.get("Numeric_Review_Version","")))
    if rows:
        write_csv_atomic(root/OUTPUT,ROW_FIELDS,rows)
    result={
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"OFFICIAL_PDF_NUMERIC_CANDIDATE_REVIEW_UPDATED",
        "source_pdf_receipts_checked":len(sources),
        "new_reports_read":done,
        "new_complete_six_field_candidate_reports":complete,
        "queued_source_reports_without_current_section_evidence":blocked[:10],
        "pending_numeric_reviews":max(0,len(pending)-done),
        "total_numeric_candidate_reports":len(rows),
        "candidate_is_not_audited_fact_verification":True,
        "official_audited_numeric_facts_promoted":0,
        "strict_fundamental_passes_added":0,
        "orders":"DISABLED",
    }
    atomic_status(root/STATUS,result)
    return result


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-files",type=int,default=1)
    args=parser.parse_args()
    try:
        result=run(data_root(),max_files=args.max_files)
    except (SectionReviewBlocked,OSError,ValueError,csv.Error) as exc:
        result={
            "state":"NUMERIC_PDF_CANDIDATE_REVIEW_BLOCKED",
            "checked_at_utc":datetime.now(timezone.utc).isoformat(),
            "reason":str(exc)[:140],
            "audited_source_history_preserved":True,
            "orders":"DISABLED",
        }
        atomic_status(data_root()/STATUS,result)
        print(json.dumps(result,indent=2))
        return 1
    print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
