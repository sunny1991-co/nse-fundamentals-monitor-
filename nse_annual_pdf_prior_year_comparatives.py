#!/usr/bin/env python3
"""Extract strictly REVIEW-ONLY previous-year comparative rows from original NSE annual PDFs.

FY2023-24 audited annual-report consolidated statements often also print
31 March 2023 amounts. Those are retrospective COMPARATIVES within a
later annual PDF, not a separately verified FY2022-23 audited NSE XBRL
filing. Never enter strict, historical XBRL, strategy or order files.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from nse_annual_pdf_numeric_review import (
    financial_page_evidence, PAIR, number, close, crore,
    OUTPUT as NUMERIC_OUTPUT, VERSION as NUMERIC_VERSION,
)
from nse_annual_pdf_section_review import (
    checked_sources, records, SectionReviewBlocked, LATEST_OUTPUT,
    VERSION as SECTION_VERSION,
)
from nse_mainboard_discovery import write_csv_atomic
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

VERSION="prior-year-comparative-candidate-v1"
OUTPUT="nse_annual_pdf_prior_year_comparative_candidates.csv"
STATUS="runtime/nse_annual_pdf_prior_year_comparative_status.json"
MAX_FILES=2
SOURCE_FY="2023-24"
PRIOR_FY="2022-23"
CORE=("Revenue_Operations_Cr","Net_Profit_Cr","Basic_EPS_Rs",
      "Total_Assets_Cr","Total_Equity_Cr","Total_Borrowings_Cr")
AUX={
    "NCI":"Noncontrolling_Interest_Cr",
    "Non_Current_Assets":None,"Current_Assets":None,
    "Equity_And_Liabilities":None,"Liabilities":None,
    "Tax":None,"Profit_Before_Tax":None,
    "Borrowings_Current":"Current_Borrowings_Cr",
    "Borrowings_Noncurrent":"Noncurrent_Borrowings_Cr",
}
FIELDS=[
    "Symbol","ISIN","Source_Report_Financial_Year",
    "Comparative_Financial_Year","Comparative_Fiscal_Year_End",
    "Official_NSE_PDF_URL","Official_NSE_PDF_SHA256",
    "Source_PDF_Numeric_Review_Version","Comparative_Review_Version",
    "Comparative_Evidence_Type","Unit","Review_State",
    *CORE,"Noncurrent_Borrowings_Cr","Current_Borrowings_Cr",
    "Noncontrolling_Interest_Cr","Candidate_Core_Fields",
    "Comparative_Balance_Sheet_Check","Comparative_Profit_Tax_Check",
    "Comparative_Borrowings_Check","Evidence_Rows_JSON",
    "Review_Issues_JSON","Separately_Audited_Prior_Year_XBRL_Verified",
    "Strict_Fundamental_Pass","Audited_Facts_Promoted","Orders",
    "Reviewed_At_UTC",
]


def prior_column(evidence:dict,field:str,expected_current:Decimal,
                 required_page:int)->tuple[Decimal,dict]:
    """Take the SECOND printed year only if both amounts are source-attested.

    Exclude adjacent narrative, a third decimal token, or any cached
    current figure inconsistent with the original PDF's row.
    """
    row=evidence.get(field)
    if not isinstance(row,dict) or row.get("page_1based")!=required_page:
        raise SectionReviewBlocked("COMPARATIVE_EVIDENCE_PAGE_OR_METRIC_MISSING")
    raw=row.get("raw_source_row")
    if not isinstance(raw,str) or len(raw)>360:
        raise SectionReviewBlocked("COMPARATIVE_SOURCE_ROW_UNSUPPORTED")
    matched=PAIR.findall(raw)
    if len(matched)!=2:
        raise SectionReviewBlocked("COMPARATIVE_REQUIRES_EXACTLY_TWO_NUMERIC_YEAR_COLUMNS")
    try:
        first,prior=(number(x) for x in matched)
        recorded=number(row.get("current_lakhs_or_eps",
                                row.get("current_lakhs","")))
    except (ValueError,InvalidOperation,TypeError) as exc:
        raise SectionReviewBlocked("COMPARATIVE_RECORDED_NUMBER_INVALID") from exc
    if first!=expected_current or recorded!=expected_current:
        raise SectionReviewBlocked("COMPARATIVE_FIRST_COLUMN_NOT_ATTESTED_CURRENT_YEAR")
    return prior,{
        "source_original_row":raw,
        "source_page_1based":required_page,
        "prior_year_lakhs_or_eps":str(prior),
        "pdf_extraction_mode":row.get("pdf_extraction_mode",""),
        "source_report_fy":SOURCE_FY,
        "comparison_year":PRIOR_FY,
    }


def comparative_evidence(pdf_bytes:bytes,section:dict,confirmed_current:dict,
                         *,reader_cls=None)->dict:
    """Reopen original PDF and confirm exact same six present-year metrics."""
    report=financial_page_evidence(pdf_bytes,section,reader_cls=reader_cls)
    if (report["state"]!="COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED"
        or report["units"]!="INR_LAKHS"
        or report["fy_end"]!="2024-03-31"
        or confirmed_current.get("Numeric_Review_Version")!=NUMERIC_VERSION
        or confirmed_current.get("Section_Review_Version")!=SECTION_VERSION
        or confirmed_current.get("Review_State")!=report["state"]
        or confirmed_current.get("Candidate_Core_Fields")!="6"
        or confirmed_current.get("Audited_Consolidated_Facts_Verified","").lower()!="false"
        or confirmed_current.get("Strict_Fundamental_Pass","").lower()!="false"
        or confirmed_current.get("Orders")!="DISABLED"):
        raise SectionReviewBlocked("CURRENT_YEAR_PDF_CANDIDATE_NOT_READY_OR_CONFLICTING")
    for field in CORE:
        if confirmed_current.get(field)!=report["metrics"].get(field):
            raise SectionReviewBlocked("CACHED_CURRENT_FINANCIAL_FACT_VS_PDF_CONFLICT")
    pages=report["pages"]
    evidence=report["raw_evidence"]
    figures={}
    detailed={}
    def fetch(k,unit_field,page):
        source_value=number(evidence[k].get(unit_field,""))
        val,proof=prior_column(
            evidence,k,source_value,pages[page])
        detailed[k]=proof
        return val

    # Currency values are in LAKHS in the printed report; EPS in INR/share.
    for field in CORE:
        if field=="Total_Borrowings_Cr":
            continue
        k=field
        page="profit_loss" if field in (
            "Revenue_Operations_Cr","Net_Profit_Cr","Basic_EPS_Rs"
        ) else "balance_sheet"
        val=fetch(k,"current_lakhs_or_eps",page)
        figures[k]=val
    aux={}
    for key in AUX:
        page="profit_loss" if key in ("Profit_Before_Tax","Tax") else "balance_sheet"
        value=fetch(key,"current_lakhs",page)
        aux[key]=value
    figures["Total_Borrowings_Cr"]=(
        aux["Borrowings_Current"]+aux["Borrowings_Noncurrent"])
    # Rounding tolerance only permits statement-level arithmetic
    # (hundredths of lakh), not source or presentation differences.
    asset=figures["Total_Assets_Cr"]
    bs_ok=all([
        close(asset,aux["Non_Current_Assets"]+aux["Current_Assets"]),
        close(asset,aux["Equity_And_Liabilities"]),
        close(asset,figures["Total_Equity_Cr"]+aux["NCI"]+aux["Liabilities"]),
    ])
    profit_ok=close(aux["Profit_Before_Tax"]-aux["Tax"],
                    figures["Net_Profit_Cr"])
    # Prior-year printed columns require distinct positive debt section
    # values exactly like the source current column. No leases are added.
    debts_ok=all(aux[k]>=0 for k in (
        "Borrowings_Current","Borrowings_Noncurrent"))
    if not (bs_ok and profit_ok and debts_ok):
        issues=[]
        if not bs_ok: issues.append("PRIOR_YEAR_BALANCE_ARITHMETIC_CONFLICT")
        if not profit_ok: issues.append("PRIOR_YEAR_PROFIT_TAX_ARITHMETIC_CONFLICT")
        if not debts_ok: issues.append("PRIOR_YEAR_BORROWINGS_INVALID")
        return {
            "state":"PRIOR_YEAR_COMPARATIVES_ARITHMETIC_REVIEW_REQUIRED",
            "candidate":{},"evidence":detailed,"issues":issues,
            "balance_check":"SOURCE_ARITHMETIC_CONFLICT" if not bs_ok else "SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY",
            "profit_check":"SOURCE_ARITHMETIC_CONFLICT" if not profit_ok else "SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY",
            "debt_check":"INVALID" if not debts_ok else "BOTH_PREVIOUS_YEAR_ROWS_FOUND_UNVERIFIED",
        }
    values={
        field:(str(figures[field].quantize(Decimal("0.01")))
               if field=="Basic_EPS_Rs" else crore(figures[field]))
        for field in CORE
    }
    values["Noncurrent_Borrowings_Cr"]=crore(aux["Borrowings_Noncurrent"])
    values["Current_Borrowings_Cr"]=crore(aux["Borrowings_Current"])
    values["Noncontrolling_Interest_Cr"]=crore(aux["NCI"])
    return {
        "state":"ALL_SIX_PRIOR_YEAR_COMPARATIVE_SOURCE_CANDIDATES_NOT_AUDIT_VERIFIED",
        "candidate":values,"evidence":detailed,"issues":[],
        "balance_check":"SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY",
        "profit_check":"SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY",
        "debt_check":"BOTH_PREVIOUS_YEAR_ROWS_FOUND_UNVERIFIED",
    }


def run(root:Path,*,max_files:int=1,now=None,reader_cls=None)->dict:
    if not 1<=max_files<=MAX_FILES:
        raise ValueError("MAX_FILES_MUST_BE_1_OR_2")
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    # NSE current mainboard identity, report's NSE annual archive metadata,
    # cached PDF URL/path and SHA256 receipt are independently attested.
    sources=checked_sources(root,now)
    originals=records(root/LATEST_OUTPUT)
    numeric=records(root/NUMERIC_OUTPUT)
    sections={
        (r.get("Symbol"),r.get("Financial_Year"),
         r.get("Official_Report_URL"),r.get("PDF_SHA256")):r
        for r in originals if r.get("Review_Version")==SECTION_VERSION
        and r.get("Section_Review_State")==
            "CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED"
    }
    current={
        (r.get("Symbol"),r.get("Financial_Year"),
         r.get("Official_Report_URL"),r.get("PDF_SHA256")):r
        for r in numeric if r.get("Numeric_Review_Version")==NUMERIC_VERSION
    }
    old=records(root/OUTPUT)
    archive={
        (r.get("Symbol"),r.get("Source_Report_Financial_Year"),
         r.get("Official_NSE_PDF_SHA256"),r.get("Comparative_Review_Version")):r
        for r in old
    }
    pending=[]
    deferred_incomplete=[]
    for receipt,entry,path in sources:
        if receipt["Financial_Year"]!=SOURCE_FY:
            continue
        source_key=(receipt["Symbol"],SOURCE_FY,
                    receipt["Official_Report_URL"],receipt["PDF_SHA256"])
        versioned=(receipt["Symbol"],SOURCE_FY,receipt["PDF_SHA256"],VERSION)
        if versioned in archive:
            continue
        if source_key not in sections or source_key not in current:
            continue
        row=current[source_key]
        if row.get("ISIN")!=entry["ISIN"]:
            raise SectionReviewBlocked("NUMERIC_SOURCE_COMPARATIVE_ISIN_MISMATCH")
        # An incomplete current-year numeric review is not a comparative
        # source. Defer this exact immutable PDF source without aborting the
        # other issuers in the batch. Refuse to defer malformed or already
        # populated source data under an "incomplete" review label.
        if row.get("Review_State")==(
                "INCOMPLETE_OR_AMBIGUOUS_SOURCE_NUMBERS_REVIEW_REQUIRED"):
            if (row.get("Section_Review_Version")!=SECTION_VERSION
                or row.get("Candidate_Core_Fields")!="0"
                or any(row.get(field) for field in CORE)
                or row.get("Audited_Consolidated_Facts_Verified","").lower()!="false"
                or row.get("Strict_Fundamental_Pass","").lower()!="false"
                or row.get("Orders")!="DISABLED"):
                raise SectionReviewBlocked(
                    "INCOMPLETE_CURRENT_PDF_SOURCE_STATE_CONFLICT")
            deferred_incomplete.append({
                "symbol":receipt["Symbol"],
                "source_report_fy":SOURCE_FY,
                "reason":"CURRENT_PDF_SIX_CORE_NUMERIC_EVIDENCE_INCOMPLETE",
            })
            continue
        pending.append((receipt,entry,path,sections[source_key],row,versioned))
    reviewed=0
    complete=0
    for receipt,entry,path,section,original,versioned in pending[:max_files]:
        payload=path.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=receipt["PDF_SHA256"]:
            raise SectionReviewBlocked("COMPARATIVE_SOURCE_PDF_HASH_CHANGED")
        result=comparative_evidence(payload,section,original,reader_cls=reader_cls)
        record={
            "Symbol":receipt["Symbol"],"ISIN":entry["ISIN"],
            "Source_Report_Financial_Year":SOURCE_FY,
            "Comparative_Financial_Year":PRIOR_FY,
            "Comparative_Fiscal_Year_End":"2023-03-31",
            "Official_NSE_PDF_URL":receipt["Official_Report_URL"],
            "Official_NSE_PDF_SHA256":receipt["PDF_SHA256"],
            "Source_PDF_Numeric_Review_Version":NUMERIC_VERSION,
            "Comparative_Review_Version":VERSION,
            "Comparative_Evidence_Type":"PRIOR_YEAR_COLUMN_OF_LATER_ANNUAL_REPORT_ONLY",
            "Unit":"INR_LAKHS_EXCEPT_EPS_RUPEES",
            "Review_State":result["state"],
            "Candidate_Core_Fields":sum(k in result["candidate"] for k in CORE),
            "Comparative_Balance_Sheet_Check":result["balance_check"],
            "Comparative_Profit_Tax_Check":result["profit_check"],
            "Comparative_Borrowings_Check":result["debt_check"],
            "Evidence_Rows_JSON":json.dumps(result["evidence"],sort_keys=True),
            "Review_Issues_JSON":json.dumps(result["issues"]),
            "Separately_Audited_Prior_Year_XBRL_Verified":"false",
            "Strict_Fundamental_Pass":"false",
            "Audited_Facts_Promoted":0,"Orders":"DISABLED",
            "Reviewed_At_UTC":now.astimezone(timezone.utc).isoformat(),
            **result["candidate"],
        }
        archive[versioned]=record
        reviewed+=1
        complete+=record["Candidate_Core_Fields"]==6
    rows=sorted(archive.values(),key=lambda r:(
        r.get("Symbol",""),r.get("Source_Report_Financial_Year",""),
        r.get("Official_NSE_PDF_SHA256",""),r.get("Comparative_Review_Version","")))
    if rows:
        write_csv_atomic(root/OUTPUT,FIELDS,rows)
    outcome={
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"ORIGINAL_NSE_PDF_PRIOR_YEAR_COMPARATIVE_REVIEW_UPDATED",
        "reports_reviewed_this_run":reviewed,
        "new_six_field_comparative_candidates":complete,
        "reports_pending_current_source_review":max(0,len(pending)-reviewed),
        "reports_deferred_incomplete_current_numeric_evidence":len(deferred_incomplete),
        "deferred_incomplete_source_preview":deferred_incomplete[:8],
        "total_comparative_candidate_source_reports":len(rows),
        "source_report_financial_year":SOURCE_FY,
        "separate_earlier_year_audited_xbrl_filings_verified":0,
        "strict_fundamental_passes_added":0,
        "source_index_api_calls":0,
        "paid_ai_api_calls":0,
        "orders":"DISABLED",
    }
    atomic_status(root/STATUS,outcome)
    return outcome


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-files",type=int,default=1)
    args=parser.parse_args()
    try:
        result=run(data_root(),max_files=args.max_files)
    except (SectionReviewBlocked,ValueError,InvalidOperation,OSError,csv.Error) as exc:
        result={
            "state":"PRIOR_YEAR_COMPARATIVE_REVIEW_BLOCKED",
            "reason":str(exc)[:160],
            "verified_original_files_preserved":True,
            "orders":"DISABLED",
        }
        atomic_status(data_root()/STATUS,result)
        print(json.dumps(result,indent=2))
        return 1
    print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
