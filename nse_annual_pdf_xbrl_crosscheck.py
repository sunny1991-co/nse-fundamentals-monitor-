#!/usr/bin/env python3
"""Read-only NSE archived annual PDF vs source-verified audited NSE XBRL audit.

PDF numeric candidates do NOT become audited/verified XBRL source facts when
numbers agree. This diagnostic records either a missing-original gap, an
ambiguous source revision, or a comparison. Strict 4-year/trading inputs
are NEVER written.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from nse_annual_pdf_numeric_review import OUTPUT as PDF_NUMERIC,VERSION as NUMERIC_VERSION
from nse_annual_pdf_section_review import (
    checked_sources, records, SectionReviewBlocked,
    LATEST_OUTPUT, VERSION as SECTION_VERSION,
)
from nse_mainboard_discovery import write_csv_atomic
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

HISTORY="annual_historical_results.csv"
OUTPUT="nse_annual_pdf_vs_xbrl_source_review.csv"
STATUS="runtime/nse_annual_pdf_vs_xbrl_source_review_status.json"
VERSION="NSE_PDF_XBRL_READ_ONLY_CROSSCHECK_V1"
YEARS={"2022-23":"2023-03-31","2023-24":"2024-03-31"}
METRICS=("Revenue_Operations_Cr","Net_Profit_Cr","Basic_EPS_Rs",
         "Total_Assets_Cr","Total_Equity_Cr","Total_Borrowings_Cr")
FIELDS=[
    "Symbol","ISIN","Financial_Year","Fiscal_Year_End",
    "Official_PDF_URL","Official_PDF_SHA256","Official_XBRL_URL",
    "XBRL_Filing_Published_At","PDF_Review_Version",
    "Reconciliation_Version","Review_State","Metrics_Checked",
    "Numeric_Matched","Numeric_Discrepant","Metrics_Missing_XBRL",
    "Metric_Results_JSON","Needs_Human_Accounting_Review",
    "PDF_Not_Audited_Fact_Verification","Audited_Facts_Promoted",
    "Strict_Annual_Passes_Added","Orders","Checked_At_UTC",
]


class CrosscheckBlocked(RuntimeError):
    pass


def read_bounded(path:Path,required:set[str],limit:int=30000)->list[dict]:
    if not path.is_file():
        return []
    if path.is_symlink() or path.stat().st_size>18_000_000:
        raise CrosscheckBlocked("UNSAFE_OR_OVERSIZED_SOURCE_CSV")
    try:
        with path.open(encoding="utf-8-sig",newline="") as stream:
            reader=csv.DictReader(stream)
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                raise CrosscheckBlocked("SOURCE_CSV_REQUIRED_COLUMNS_MISSING")
            rows=[]
            for row in reader:
                if len(rows)>=limit or None in row:
                    raise CrosscheckBlocked("SOURCE_CSV_BOUNDED_SCHEMA_INVALID")
                rows.append(row)
            return rows
    except (OSError,UnicodeError,csv.Error) as exc:
        raise CrosscheckBlocked("SOURCE_CSV_UNREADABLE") from exc


def official_xml(url:str)->bool:
    try:
        u=urlsplit(url)
        return (u.scheme=="https" and u.hostname=="nsearchives.nseindia.com"
                and u.port is None and not u.username and not u.password
                and not u.query and not u.fragment
                and u.path.startswith("/corporate/xbrl/")
                and u.path.lower().endswith(".xml"))
    except ValueError:
        return False


def decimal_value(raw):
    if raw is None or isinstance(raw,bool):
        return None
    try:
        value=Decimal(str(raw).replace(",","").strip())
        return value if value.is_finite() else None
    except (InvalidOperation,ValueError,TypeError):
        return None


def true(raw):
    return str(raw).strip().casefold() in ("true","yes","1")


def original_source_ok(row:dict)->bool:
    fy=row.get("Financial_Year")
    audit=str(row.get("Audit_Status","")).casefold().strip()
    return bool(
        fy in YEARS
        and row.get("Fiscal_Year_End")==YEARS[fy]
        and row.get("Reporting_Basis","").casefold().strip()=="consolidated"
        and "audit" in audit and "unaudit" not in audit and "un-audit" not in audit
        and true(row.get("NSE_Consolidation_Verified"))
        and row.get("Status") in ("VERIFIED","VERIFIED_WITH_MISSING_CORE_FIELDS")
        and official_xml(row.get("Source_URL",""))
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}",str(row.get("Filing_Published_At",""))[:10])
    )


def choose_original(rows:list[dict],symbol:str,fy:str):
    candidates=[r for r in rows if r.get("Symbol","").strip().upper()==symbol
                and r.get("Financial_Year")==fy]
    valid=[r for r in candidates if original_source_ok(r)]
    if not valid:
        return None,("ORIGINAL_NSE_XBRL_HISTORY_NOT_SOURCE_VERIFIED"
                     if candidates else "ORIGINAL_NSE_XBRL_HISTORY_NOT_AVAILABLE")
    latest=max(r["Filing_Published_At"][:10] for r in valid)
    final=[r for r in valid if r["Filing_Published_At"][:10]==latest]
    # The same day could hold multiple revisions. Exact duplicate entries
    # may be collapsed only if URL and ALL six financial values agree.
    uniqueness={
        (r["Source_URL"],tuple(str(r.get(f,"")).strip() for f in METRICS))
        for r in final
    }
    if len(uniqueness)!=1:
        return None,"AMBIGUOUS_XBRL_REVISIONS_OR_CONFLICTING_NUMERIC_FACTS"
    return final[0],None


def ready_candidate(row:dict)->bool:
    return bool(
        row.get("Numeric_Review_Version")==NUMERIC_VERSION
        and row.get("Section_Review_Version")==SECTION_VERSION
        and row.get("Financial_Year") in YEARS
        and row.get("Fiscal_Year_End")==YEARS[row["Financial_Year"]]
        and row.get("Review_State")=="COMPLETE_SOURCE_ROW_CANDIDATES_NOT_AUDIT_VERIFIED"
        and row.get("Candidate_Core_Fields")=="6"
        and row.get("Balance_Sheet_Check")=="SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY"
        and row.get("Profit_Tax_Check")=="SOURCE_ARITHMETIC_MATCH_CANDIDATE_ONLY"
        and row.get("Borrowings_Check")=="BOTH_ROW_CANDIDATES_FOUND_UNVERIFIED"
        and row.get("Audited_Consolidated_Facts_Verified","").lower()=="false"
        and row.get("Strict_Fundamental_Pass","").lower()=="false"
        and row.get("Orders")=="DISABLED"
        and all(decimal_value(row.get(f)) is not None for f in METRICS)
    )


def comparison(pdf,xml):
    result={}
    n_match=n_diff=n_missing=0
    for field in METRICS:
        candidate=decimal_value(pdf[field])
        verified=decimal_value(xml.get(field))
        if verified is None:
            outcome="ORIGINAL_XBRL_FIELD_NOT_PRESENT"
            diff=None
            n_missing+=1
        else:
            diff=abs(candidate-verified)
            # INR crore rounded from hundredths-of-lakh vs NSE XBRL
            # whole-currency values; use a very narrow absolute tolerance.
            # EPS is rupees/share, not INR crore.
            tolerance=(Decimal("0.02") if field=="Basic_EPS_Rs"
                       else max(Decimal("0.005"),
                                max(abs(candidate),abs(verified))*Decimal("0.00001")))
            outcome=("NUMERIC_WITHIN_TOLERANCE_CANDIDATE_ONLY"
                     if diff<=tolerance else "ACCOUNTING_OR_SOURCE_DIFFERENCE_REQUIRES_REVIEW")
            n_match+=outcome=="NUMERIC_WITHIN_TOLERANCE_CANDIDATE_ONLY"
            n_diff+=outcome=="ACCOUNTING_OR_SOURCE_DIFFERENCE_REQUIRES_REVIEW"
        result[field]={
            "pdf_candidate":str(candidate),
            "xbrl_original":str(verified) if verified is not None else None,
            "absolute_difference":str(diff) if diff is not None else None,
            "state":outcome,
        }
    return result,n_match,n_diff,n_missing


def run(root,*,now=None,max_files=2):
    if not 1<=max_files<=2:
        raise ValueError("MAX_FILES_1_OR_2_REQUIRED")
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    # Re-attest exact issuer+ISIN+archived original PDF receipt+local source
    # path before treating even a CSV review candidate as comparable evidence.
    sources=checked_sources(root,now)
    source_map={(receipt["Symbol"],receipt["Financial_Year"],
                 receipt["Official_Report_URL"],receipt["PDF_SHA256"]):
                (receipt,entry,path)
                for receipt,entry,path in sources}
    src=read_bounded(root/PDF_NUMERIC,{
        "Symbol","ISIN","Financial_Year","Official_Report_URL",
        "PDF_SHA256","Numeric_Review_Version","Review_State",
    })
    view=read_bounded(root/LATEST_OUTPUT,{
        "Symbol","Financial_Year","Official_Report_URL","PDF_SHA256",
        "Review_Version","Section_Review_State",
    })
    latest={(r["Symbol"],r["Financial_Year"],r["Official_Report_URL"],
             r["PDF_SHA256"]):r for r in view
            if r["Review_Version"]==SECTION_VERSION}
    historic=read_bounded(root/HISTORY,{
        "Symbol","Financial_Year","Fiscal_Year_End","Reporting_Basis",
        "Audit_Status","NSE_Consolidation_Verified","Status",
        "Source_URL","Filing_Published_At",
    })
    # Candidate reports are review artifacts; old versions are preserved but
    # they must never be counted, matched or published as a new audited fact.
    eligible=[r for r in src if ready_candidate(r)]
    if len(eligible)>10000:
        raise CrosscheckBlocked("TOO_MANY_CURRENT_PDF_CANDIDATES")
    eligible.sort(key=lambda r:(r["Symbol"],r["Financial_Year"],r["PDF_SHA256"]))
    result_rows=[]
    missing=ambiguous=mismatches=compared=0
    for pdf in eligible[:max_files]:
        key=(pdf["Symbol"],pdf["Financial_Year"],pdf["Official_Report_URL"],
             pdf["PDF_SHA256"])
        src_record=source_map.get(key)
        section=latest.get(key)
        if not src_record or not section:
            # Never reattribute an old candidate to a different PDF,
            # annual period, ISIN or section-review parser version.
            raise CrosscheckBlocked("OFFICIAL_PDF_SOURCE_ATTESTATION_MISSING")
        receipt,entry,path=src_record
        if (pdf["ISIN"]!=entry["ISIN"] or
            section.get("Section_Review_State")!=
                "CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED"):
            raise CrosscheckBlocked("PDF_ISIN_OR_SECTION_EVIDENCE_NOT_ATTESTED")
        if hashlib.sha256(path.read_bytes()).hexdigest()!=receipt["PDF_SHA256"]:
            raise CrosscheckBlocked("ARCHIVED_ORIGINAL_PDF_SOURCE_BYTES_CHANGED")
        xml,gap=choose_original(historic,pdf["Symbol"],pdf["Financial_Year"])
        results={}
        match=diff=missing_fields=0
        if gap:
            state=gap
            missing+=gap!="AMBIGUOUS_XBRL_REVISIONS_OR_CONFLICTING_NUMERIC_FACTS"
            ambiguous+=gap=="AMBIGUOUS_XBRL_REVISIONS_OR_CONFLICTING_NUMERIC_FACTS"
        else:
            results,match,diff,missing_fields=comparison(pdf,xml)
            compared+=1
            state=("PDF_VS_XBRL_DIFFERENCE_REVIEW_REQUIRED" if diff
                   else "PDF_VS_XBRL_PARTIAL_FIELD_EVIDENCE" if missing_fields
                   else "ALL_SIX_NUMERIC_VALUES_CLOSE_SOURCE_REVIEW_ONLY")
            mismatches+=bool(diff)
        result_rows.append({
            "Symbol":pdf["Symbol"],"ISIN":pdf["ISIN"],
            "Financial_Year":pdf["Financial_Year"],
            "Fiscal_Year_End":pdf["Fiscal_Year_End"],
            "Official_PDF_URL":pdf["Official_Report_URL"],
            "Official_PDF_SHA256":pdf["PDF_SHA256"],
            "Official_XBRL_URL":xml.get("Source_URL","") if xml else "",
            "XBRL_Filing_Published_At":xml.get("Filing_Published_At","") if xml else "",
            "PDF_Review_Version":NUMERIC_VERSION,
            "Reconciliation_Version":VERSION,
            "Review_State":state,
            "Metrics_Checked":len(results),"Numeric_Matched":match,
            "Numeric_Discrepant":diff,"Metrics_Missing_XBRL":missing_fields,
            "Metric_Results_JSON":json.dumps(results,sort_keys=True),
            "Needs_Human_Accounting_Review":"true",
            "PDF_Not_Audited_Fact_Verification":"true",
            "Audited_Facts_Promoted":0,"Strict_Annual_Passes_Added":0,
            "Orders":"DISABLED","Checked_At_UTC":now.astimezone(timezone.utc).isoformat(),
        })
    write_csv_atomic(root/OUTPUT,FIELDS,result_rows)
    summary={
        "state":"READ_ONLY_PDF_VS_XBRL_SOURCE_REVIEW_UPDATED",
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "complete_pdf_candidate_rows":len(eligible),
        "reports_checked":len(result_rows),
        "remaining_source_candidate_reports":max(0,len(eligible)-len(result_rows)),
        "source_verified_original_xbrl_years_compared":compared,
        "missing_original_xbrl_years":missing,
        "ambiguous_original_xbrl_revision_years":ambiguous,
        "reports_with_accounting_differences":mismatches,
        "no_official_xbrl_numeric_fact_promoted":True,
        "strict_four_year_passes_added":0,
        "orders":"DISABLED",
    }
    atomic_status(root/STATUS,summary)
    return summary


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-files",type=int,default=2)
    args=parser.parse_args()
    try:
        outcome=run(data_root(),max_files=args.max_files)
    except (SectionReviewBlocked,CrosscheckBlocked,OSError,ValueError,csv.Error) as exc:
        outcome={
            "state":"PDF_XBRL_SOURCE_REVIEW_BLOCKED",
            "checked_at_utc":datetime.now(timezone.utc).isoformat(),
            "reason":str(exc)[:140],
            "original_financials_preserved":True,
            "orders":"DISABLED",
        }
        atomic_status(data_root()/STATUS,outcome)
        print(json.dumps(outcome,indent=2))
        return 1
    print(json.dumps(outcome,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
