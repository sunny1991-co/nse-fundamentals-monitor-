#!/usr/bin/env python3
"""FY2022-23 vs FY2023-24 research-only YoY from one immutable NSE annual PDF.

This is NOT separately verified annual XBRL for either printed column.
No strict investment signals, trade shortlist, backtest or broker outputs.
A previously-saved candidate CSV must exactly match original PDF bytes,
source pages and six parsed values before any comparison is published.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path

from nse_annual_pdf_numeric_review import (
    OUTPUT as CURRENT_OUTPUT, VERSION as CURRENT_VERSION,
    financial_page_evidence,
)
from nse_annual_pdf_prior_year_comparatives import (
    OUTPUT as PRIOR_OUTPUT, VERSION as PRIOR_VERSION,
    comparative_evidence, CORE, SOURCE_FY, PRIOR_FY,
)
from nse_annual_pdf_section_review import (
    LATEST_OUTPUT, VERSION as SECTION_VERSION,
    checked_sources, records, SectionReviewBlocked,
)
from nse_mainboard_discovery import write_csv_atomic
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

OUTPUT="nse_annual_pdf_two_year_research_comparison.csv"
STATUS="runtime/nse_annual_pdf_two_year_research_status.json"
VERSION="source-locked-pdf-two-year-yoy-research-v2-section-attested"
MAX_FILES=2

# Six core figures are labelled identically in both observations.
METRICS=CORE
PROVENANCE=[
    "Symbol","ISIN","Source_Report_Financial_Year",
    "Prior_Comparative_Financial_Year","Current_Reported_Financial_Year",
    "Original_NSE_PDF_URL","Original_NSE_PDF_SHA256",
    "Current_PDF_Review_Version","Current_PDF_Section_Review_Version",
    "Prior_Column_Review_Version",
    "Two_Year_Research_Version","State","Comparison_Field_Count",
    "Current_Column_Audit_Status","Prior_Column_Audit_Status",
    "Reporting_And_Accounting_Review_Required",
    "Source_Audited_Annual_XBRL_Passes_Added",
    "Strict_Fundamental_Passes_Added","Investment_Signal","Orders",
    "Reviewed_At_UTC",
]
METRIC_FIELDS=[
    key for metric in METRICS
    for key in (
        f"Prior_{metric}",f"Current_{metric}",
        f"Absolute_Change_{metric}",f"YoY_Percent_{metric}",
        f"YoY_Interpretation_{metric}"
    )
]
RATIO_FIELDS=[
    "Prior_Net_Profit_Margin_Pct","Current_Net_Profit_Margin_Pct",
    "Prior_Borrowings_To_Equity","Current_Borrowings_To_Equity",
    "Metric_Evidence_Pages_JSON",
]
FIELDS=[*PROVENANCE,*METRIC_FIELDS,*RATIO_FIELDS]


class PDFComparisonBlocked(RuntimeError):
    pass


def decimal(raw)->Decimal:
    if isinstance(raw,bool) or raw is None:
        raise PDFComparisonBlocked("MISSING_OR_NONNUMERIC_FINANCIAL_CANDIDATE")
    try:
        value=Decimal(str(raw).strip())
        if not value.is_finite():
            raise PDFComparisonBlocked("NONFINITE_FINANCIAL_CANDIDATE")
        return value
    except (ValueError,InvalidOperation) as exc:
        raise PDFComparisonBlocked("MISSING_OR_NONNUMERIC_FINANCIAL_CANDIDATE") from exc


def comparable_percent(current:Decimal,previous:Decimal)->tuple[str,str]:
    """Disallow misleading percent growth from zero/negative base.

    Absolute changes remain visible even when a true percentage growth
    interpretation is mathematically inappropriate (e.g. loss recovery).
    """
    if previous<=0:
        return "","PERCENT_NOT_MEANINGFUL_PRIOR_ZERO_OR_NEGATIVE"
    pct=(current-previous)/previous*Decimal("100")
    return str(pct.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)),"POSITIVE_BASE_VALID"


def pct_margin(numerator,denominator):
    if denominator<=0:
        return ""
    return str((numerator/denominator*Decimal("100")).quantize(
        Decimal("0.01"),rounding=ROUND_HALF_UP))


def leverage(debt,equity):
    if equity<=0:
        return ""
    return str((debt/equity).quantize(Decimal("0.0001"),rounding=ROUND_HALF_UP))


def one_record(entry,receipt,section,current,prior,payload,now,*,reader_cls=None):
    key=(receipt["Symbol"],SOURCE_FY,
         receipt["Official_Report_URL"],receipt["PDF_SHA256"])
    if (
        current.get("Symbol")!=key[0] or current.get("ISIN")!=entry["ISIN"]
        or current.get("Financial_Year")!=SOURCE_FY
        or current.get("Official_Report_URL")!=key[2]
        or current.get("PDF_SHA256")!=key[3]
        or current.get("Fiscal_Year_End")!="2024-03-31"
        or current.get("Numeric_Review_Version")!=CURRENT_VERSION
        or current.get("Section_Review_Version")!=SECTION_VERSION
        or current.get("Review_State")!="COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED"
        or current.get("Candidate_Core_Fields")!="6"
        or current.get("Statement_Units")!="INR_LAKHS"
        or current.get("Balance_Sheet_Check")!="SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY"
        or current.get("Profit_Tax_Check")!="SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY"
        or current.get("Borrowings_Check")!="BOTH_ROW_CANDIDATES_FOUND_UNVERIFIED"
        or current.get("Audited_Consolidated_Facts_Verified","").lower()!="false"
        or current.get("Strict_Fundamental_Pass","").lower()!="false"
        or current.get("Orders")!="DISABLED"
    ):
        raise PDFComparisonBlocked("CURRENT_PDF_SOURCE_REVIEW_ATTESTATION_MISSING")
    if (
        prior.get("Symbol")!=key[0] or prior.get("ISIN")!=entry["ISIN"]
        or prior.get("Source_Report_Financial_Year")!=SOURCE_FY
        or prior.get("Comparative_Financial_Year")!=PRIOR_FY
        or prior.get("Comparative_Fiscal_Year_End")!="2023-03-31"
        or prior.get("Official_NSE_PDF_URL")!=key[2]
        or prior.get("Official_NSE_PDF_SHA256")!=key[3]
        or prior.get("Source_PDF_Numeric_Review_Version")!=CURRENT_VERSION
        or prior.get("Comparative_Review_Version")!=PRIOR_VERSION
        or prior.get("Comparative_Evidence_Type")!="PRIOR_YEAR_COLUMN_OF_LATER_ANNUAL_REPORT_ONLY"
        or prior.get("Unit")!="INR_LAKHS_EXCEPT_EPS_RUPEES"
        or prior.get("Review_State")!="ALL_SIX_PRIOR_YEAR_COMPARATIVE_SOURCE_CANDIDATES_NOT_AUDIT_VERIFIED"
        or prior.get("Candidate_Core_Fields")!="6"
        or prior.get("Comparative_Balance_Sheet_Check")!="SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY"
        or prior.get("Comparative_Profit_Tax_Check")!="SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY"
        or prior.get("Comparative_Borrowings_Check")!="BOTH_PREVIOUS_YEAR_ROWS_FOUND_UNVERIFIED"
        or prior.get("Separately_Audited_Prior_Year_XBRL_Verified","").lower()!="false"
        or prior.get("Strict_Fundamental_Pass","").lower()!="false"
        or prior.get("Audited_Facts_Promoted")!="0"
        or prior.get("Orders")!="DISABLED"
    ):
        raise PDFComparisonBlocked("PRIOR_COMPARATIVE_SOURCE_REVIEW_ATTESTATION_MISSING")
    if hashlib.sha256(payload).hexdigest()!=key[3]:
        raise PDFComparisonBlocked("SOURCE_PDF_CONTENT_SHA256_CHANGED")
    # Recompute all rows from the ORIGINAL annual PDF at the same
    # audited-consolidated pages; trust neither cached CSV nor OCR.
    reopened=financial_page_evidence(payload,section,reader_cls=reader_cls)
    if reopened["state"]!="COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED":
        raise PDFComparisonBlocked("ORIGINAL_CURRENT_PDF_EVIDENCE_INCOMPLETE")
    recomputed_prior=comparative_evidence(
        payload,section,current,reader_cls=reader_cls)
    if (recomputed_prior["state"]!=
            "ALL_SIX_PRIOR_YEAR_COMPARATIVE_SOURCE_CANDIDATES_NOT_AUDIT_VERIFIED"):
        raise PDFComparisonBlocked("ORIGINAL_PRIOR_PDF_EVIDENCE_INCOMPLETE")
    for field in METRICS:
        if (reopened["metrics"].get(field)!=current.get(field)
            or recomputed_prior["candidate"].get(field)!=prior.get(field)):
            raise PDFComparisonBlocked("CACHED_VALUE_CONFLICTS_WITH_ORIGINAL_NSE_PDF")
    prior_values={k:decimal(prior[k]) for k in METRICS}
    current_values={k:decimal(current[k]) for k in METRICS}
    result={
        "Symbol":entry["Symbol"] if "Symbol" in entry else receipt["Symbol"],
        "ISIN":entry["ISIN"],
        "Source_Report_Financial_Year":SOURCE_FY,
        "Prior_Comparative_Financial_Year":PRIOR_FY,
        "Current_Reported_Financial_Year":SOURCE_FY,
        "Original_NSE_PDF_URL":key[2],
        "Original_NSE_PDF_SHA256":key[3],
        "Current_PDF_Review_Version":CURRENT_VERSION,
        "Current_PDF_Section_Review_Version":SECTION_VERSION,
        "Prior_Column_Review_Version":PRIOR_VERSION,
        "Two_Year_Research_Version":VERSION,
        "State":"TWO_YEAR_PDF_COMPARATIVE_RESEARCH_ONLY",
        "Comparison_Field_Count":len(METRICS),
        "Current_Column_Audit_Status":"PDF_ROW_CANDIDATE_NOT_ORIGINAL_XBRL_VERIFIED",
        "Prior_Column_Audit_Status":"LATER_REPORT_COMPARATIVE_NOT_ORIGINAL_XBRL_VERIFIED",
        "Reporting_And_Accounting_Review_Required":"true",
        "Source_Audited_Annual_XBRL_Passes_Added":0,
        "Strict_Fundamental_Passes_Added":0,
        "Investment_Signal":"NONE_RESEARCH_ONLY",
        "Orders":"DISABLED",
        "Reviewed_At_UTC":now.astimezone(timezone.utc).isoformat(),
    }
    for field in METRICS:
        previous=prior_values[field]
        current_value=current_values[field]
        growth,interpretation=comparable_percent(current_value,previous)
        result[f"Prior_{field}"]=str(previous)
        result[f"Current_{field}"]=str(current_value)
        result[f"Absolute_Change_{field}"]=str(current_value-previous)
        result[f"YoY_Percent_{field}"]=growth
        result[f"YoY_Interpretation_{field}"]=interpretation

    result.update({
        "Prior_Net_Profit_Margin_Pct":pct_margin(
            prior_values["Net_Profit_Cr"],prior_values["Revenue_Operations_Cr"]),
        "Current_Net_Profit_Margin_Pct":pct_margin(
            current_values["Net_Profit_Cr"],current_values["Revenue_Operations_Cr"]),
        "Prior_Borrowings_To_Equity":leverage(
            prior_values["Total_Borrowings_Cr"],prior_values["Total_Equity_Cr"]),
        "Current_Borrowings_To_Equity":leverage(
            current_values["Total_Borrowings_Cr"],current_values["Total_Equity_Cr"]),
        "Metric_Evidence_Pages_JSON":json.dumps({
            "current":reopened["pages"],
            "prior_from_source_report":{
                k:recomputed_prior["evidence"][k]["source_page_1based"]
                for k in (*METRICS[:-1], "Borrowings_Current","Borrowings_Noncurrent")
            },
        },sort_keys=True),
    })
    return result


def unique_map(rows,*,fields,condition=None):
    mapping={}
    for row in rows:
        if condition is not None and not condition(row):
            continue
        key=tuple(row.get(k) for k in fields)
        if key in mapping:
            raise PDFComparisonBlocked("AMBIGUOUS_DUPLICATE_CURRENT_VERSION_SOURCE_ROW")
        mapping[key]=row
    return mapping


def run(root,*,max_files=1,now=None,reader_cls=None):
    if not 1<=max_files<=MAX_FILES:
        raise ValueError("MAX_FILES_1_OR_2_REQUIRED")
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    sources=checked_sources(root,now)
    now_rows=records(root/CURRENT_OUTPUT)
    older_rows=records(root/PRIOR_OUTPUT)
    reviews=records(root/LATEST_OUTPUT)
    index_fields=("Symbol","Financial_Year","Official_Report_URL","PDF_SHA256")
    current=unique_map(
        now_rows,fields=index_fields,
        # A numeric parser version alone is not enough: old v3 and new v4
        # section locators can coexist for the same PDF SHA. Only the
        # exact current section-attested numeric record is eligible.
        condition=lambda r:(r.get("Numeric_Review_Version")==CURRENT_VERSION
                            and r.get("Section_Review_Version")==SECTION_VERSION))
    prior=unique_map(
        older_rows,fields=(
            "Symbol","Source_Report_Financial_Year",
            "Official_NSE_PDF_URL","Official_NSE_PDF_SHA256"),
        condition=lambda r:r.get("Comparative_Review_Version")==PRIOR_VERSION)
    section=unique_map(
        reviews,fields=index_fields,
        condition=lambda r:r.get("Review_Version")==SECTION_VERSION)
    existing=unique_map(
        records(root/OUTPUT),
        fields=("Symbol","Original_NSE_PDF_SHA256","Two_Year_Research_Version"),
    )
    pending=[]
    for receipt,entry,path in sources:
        if receipt["Financial_Year"]!=SOURCE_FY:
            continue
        key=(receipt["Symbol"],SOURCE_FY,
             receipt["Official_Report_URL"],receipt["PDF_SHA256"])
        cmp_key=key
        if key not in current or cmp_key not in prior or key not in section:
            continue
        if (section[key].get("Section_Review_State")!=
                "CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED"):
            raise PDFComparisonBlocked("SECTION_PAGE_SOURCE_REVIEW_MISSING")
        record_key=(receipt["Symbol"],receipt["PDF_SHA256"],VERSION)
        if record_key in existing:
            continue
        pending.append((receipt,entry,path,current[key],prior[cmp_key],
                        section[key],record_key))
    done=0
    for receipt,entry,path,cur,previous,pages,key in pending[:max_files]:
        result=one_record(entry,receipt,pages,cur,previous,
                          path.read_bytes(),now,reader_cls=reader_cls)
        existing[key]=result
        done+=1
    rows=sorted(existing.values(),key=lambda r:(
        r.get("Symbol",""),r.get("Original_NSE_PDF_SHA256",""),
        r.get("Two_Year_Research_Version","")))
    if rows:
        write_csv_atomic(root/OUTPUT,FIELDS,rows)
    status={
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"SOURCE_LOCKED_TWO_YEAR_PDF_RESEARCH_COMPARISON_UPDATED",
        "source_pdf_reports_checked":len(sources),
        "new_two_year_comparisons":done,
        "total_two_year_comparisons":len(rows),
        "current_version_comparison_records":sum(
            r.get("Two_Year_Research_Version")==VERSION for r in rows),
        "prior_version_comparison_records_preserved":sum(
            r.get("Two_Year_Research_Version")!=VERSION for r in rows),
        "pending_source_locked_two_year_comparisons":max(0,len(pending)-done),
        "comparison_is_not_original_audited_xbrl_verification":True,
        "original_nse_xbrl_facts_promoted":0,
        "strict_fundamental_passes_added":0,
        "source_api_calls":0,
        "paid_ai_api_calls":0,
        "investment_signals_created":0,
        "orders":"DISABLED",
    }
    atomic_status(root/STATUS,status)
    return status


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-files",type=int,default=1)
    args=parser.parse_args()
    try:
        result=run(data_root(),max_files=args.max_files)
    except (PDFComparisonBlocked,SectionReviewBlocked,ValueError,
            InvalidOperation,OSError,csv.Error) as exc:
        result={
            "state":"TWO_YEAR_PDF_RESEARCH_COMPARISON_BLOCKED",
            "reason":str(exc)[:160],
            "audited_history_and_strategy_files_preserved":True,
            "orders":"DISABLED",
        }
        atomic_status(data_root()/STATUS,result)
        print(json.dumps(result,indent=2))
        return 1
    print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
