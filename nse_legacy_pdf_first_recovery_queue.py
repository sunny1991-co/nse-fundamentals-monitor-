#!/usr/bin/env python3
"""Offline source-first plan: independently archived NSE annual PDF evidence.

The previous original NSE old-year XBRL source index had sustained near-100%
strict annual context rejections. This plans alternative official PDF source
work without extra HTTP, without treating later PDF comparison columns as
separately audited prior-year XBRL, and without trade/strategy side effects.
"""
from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path

from nse_legacy_annual_evidence_queue import (
    snapshot as build_source_queue,OUTPUT as SOURCE_QUEUE,
    read_table,CoverageBlocked,
)
from nse_official_annual_pdf_auto_index import (
    baseline_coverage,LEDGER as PDF_LEDGER,
    source_access_cooldown,OfficialAnnualPdfUnavailable,
)
from nse_legacy_annual_recovery import verified_legacy_years
from nse_mainboard_discovery import CATALOG,write_csv_atomic
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

OUTPUT="nse_legacy_pdf_first_recovery_queue.csv"
STATUS="runtime/nse_legacy_pdf_first_recovery_status.json"
FIELDS=[
    "Symbol","ISIN","Company","Baseline_Tracked",
    "FY2024_25_Baseline_Row_Present","FY2025_26_Baseline_Row_Present",
    "Two_Newer_FYs_Baseline_Row_Count",
    "Two_Newer_FYs_Six_Core_Row_Count_Not_Four_Year_Pass",
    "FY2022_23_Source_Stage","FY2023_24_Source_Stage",
    "FY2022_23_Separate_PDF_Evidence_Stage",
    "FY2023_24_Separate_PDF_Evidence_Stage",
    "FY2022_23_Official_PDF_Links",
    "FY2023_24_Official_PDF_Links",
    "FY2023_24_Original_PDF_Receipt_Count",
    "FY2022_23_Original_XBRL_Already_Verified_Locally",
    "FY2023_24_Original_XBRL_Already_Verified_Locally",
    "FY2022_23_From_Later_PDF_Only_Not_Original_XBRL",
    "Official_Annual_Reports_Index_Prior_Check_State",
    "Official_Annual_Reports_Index_Prior_Check_UTC",
    "Original_NSE_Annual_API_Cooldown_Until_UTC",
    "PDF_First_Research_Priority","Next_Research_Action",
    "No_New_Source_Facts_Verified","Strict_Fundamental_Passes_Added",
    "Trade_Eligible","Orders",
]

class PDFRecoveryBlocked(RuntimeError):
    pass


def stage_next(fy22,fy23,verified22,verified23,previous):
    """Never silently infer separate original audited XBRL from PDF figures."""
    pdf23=fy23["Independent_PDF_Evidence_Stage"]
    pdf22=fy22["Independent_PDF_Evidence_Stage"]
    if verified22 and verified23:
        return 7,"INDEPENDENT_OLD_FY_XBRL_ALREADY_PRESENT_REVIEW_STRICT_FOUR_YEAR_SCREEN"
    if pdf23=="CURRENT_FY_SIX_PDF_CORE_CANDIDATES_UNVERIFIED":
        if pdf22=="LATER_YEAR_REPORT_PRIOR_FY_COMPARATIVE_ONLY":
            return 5,"BOTH_OLD_FYS_PDF_RESEARCH_ONLY_SEPARATE_AUDITED_XML_STILL_REQUIRED"
        return 1,"REVIEW_PRIOR_FY_COMPARATIVE_COLUMN_OF_SOURCE_2023_24_REPORT"
    if pdf23=="SOURCE_CONSOLIDATED_FINANCIAL_PAGES_FOUND":
        return 0,"EXTRACT_SIX_CURRENT_FY_PDF_FIELDS_AFTER_SOURCE_ARITHMETIC_REVIEW"
    if pdf23=="ORIGINAL_SOURCE_PDF_CACHED_RECEIPT":
        return 0,"REVIEW_ORIGINAL_FY2023_24_NSE_PDF_CONSOLIDATED_PAGES"
    if pdf23=="OFFICIAL_NSE_PDF_URL_INDEXED":
        return 0,"ARCHIVE_ALREADY_INDEXED_OFFICIAL_FY2023_24_PDF_WHEN_SOURCE_PERMITTED"
    if pdf22=="OFFICIAL_NSE_PDF_URL_INDEXED":
        return 1,"ARCHIVE_ALREADY_INDEXED_OFFICIAL_FY2022_23_PDF_WHEN_SOURCE_PERMITTED"
    if pdf22=="ORIGINAL_SOURCE_PDF_CACHED_RECEIPT":
        return 1,"REVIEW_ORIGINAL_FY2022_23_NSE_PDF_CONSOLIDATED_PAGES"
    if previous.get("Status")=="OFFICIAL_METADATA_RECEIVED":
        return 4,"REVIEW_OTHER_OFFICIAL_ANNUAL_REPORT_ARCHIVE_SOURCE_NO_PRIOR_OLD_FY_PDF_LINK"
    return 2,"INDEX_ORIGINAL_FY2023_24_NSE_ANNUAL_PDF_FIRST_TO_GET_BOTH_OLD_FY_COLUMNS"


def snapshot(root,*,now=None):
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")

    # Every source stage is freshly recomputed from current attested EQ/ISIN
    # mainboard and private candidate receipts. This also catches a missing,
    # stale or tampered base queue before we publish new source priorities.
    source_report=build_source_queue(root,now=now)
    source_rows=read_table(root/SOURCE_QUEUE,limit=5000)
    companies=read_table(root/CATALOG,limit=5000)
    if (len(source_rows)!=2*len(companies)
        or len(companies)!=source_report["official_mainboard_eq_companies"]):
        raise PDFRecoveryBlocked("CURRENT_MAINBOARD_TWO_FY_SOURCE_QUEUE_INCOMPLETE")
    verified_older=verified_legacy_years(root)
    present,complete=baseline_coverage(root)
    cooldown=source_access_cooldown(root,now)
    previous_ledger=read_table(root/PDF_LEDGER,limit=6000)
    metadata={}
    for row in previous_ledger:
        symbol=row.get("Symbol","")
        if symbol in metadata:
            raise PDFRecoveryBlocked("DUPLICATE_OFFICIAL_NSE_PDF_INDEX_ATTEMPT_LEDGER")
        metadata[symbol]=row

    by_company={}
    for row in source_rows:
        symbol=row.get("Symbol","")
        fy=row.get("Financial_Year","")
        key=(symbol,fy)
        if fy not in ("2022-23","2023-24") or key in by_company:
            raise PDFRecoveryBlocked("UNEXPECTED_OR_DUPLICATED_NSE_FY_SOURCE_SLOT")
        by_company[key]=row

    rows=[]
    actions=Counter()
    for company in companies:
        symbol=company["Symbol"]
        i22=by_company.get((symbol,"2022-23"))
        i23=by_company.get((symbol,"2023-24"))
        if not i22 or not i23 or any(
            old.get("ISIN")!=company.get("ISIN")
            for old in (i22,i23)
        ):
            raise PDFRecoveryBlocked("OLD_FY_MAINBOARD_EQ_SYMBOL_ISIN_NOT_MATCHED")
        v22=(symbol,"2022-23") in verified_older
        v23=(symbol,"2023-24") in verified_older
        prior=metadata.get(symbol,{})
        priority,action=stage_next(i22,i23,v22,v23,prior)
        actions[action]+=1
        rows.append({
            "Symbol":symbol,"ISIN":company["ISIN"],
            "Company":company["Company"],
            "Baseline_Tracked":company.get("Baseline_Tracked","false"),
            "FY2024_25_Baseline_Row_Present":
                "true" if present.get(symbol,0)>=1 else "false",
            "FY2025_26_Baseline_Row_Present":
                "unknown_per_year_see_two_fy_total",
            "Two_Newer_FYs_Baseline_Row_Count":present.get(symbol,0),
            "Two_Newer_FYs_Six_Core_Row_Count_Not_Four_Year_Pass":
                complete.get(symbol,0),
            "FY2022_23_Source_Stage":i22["Source_Stage"],
            "FY2023_24_Source_Stage":i23["Source_Stage"],
            "FY2022_23_Separate_PDF_Evidence_Stage":
                i22["Independent_PDF_Evidence_Stage"],
            "FY2023_24_Separate_PDF_Evidence_Stage":
                i23["Independent_PDF_Evidence_Stage"],
            "FY2022_23_Official_PDF_Links":i22["Official_PDF_Index_Links"],
            "FY2023_24_Official_PDF_Links":i23["Official_PDF_Index_Links"],
            "FY2023_24_Original_PDF_Receipt_Count":
                i23["Official_Cached_PDF_Source_Receipts"],
            "FY2022_23_Original_XBRL_Already_Verified_Locally":
                "true" if v22 else "false",
            "FY2023_24_Original_XBRL_Already_Verified_Locally":
                "true" if v23 else "false",
            "FY2022_23_From_Later_PDF_Only_Not_Original_XBRL":
                i22["Previous_FY_Comparative_Six_Core_From_Later_PDF"],
            "Official_Annual_Reports_Index_Prior_Check_State":
                prior.get("Status","NEVER_CHECKED_LOCALLY"),
            "Official_Annual_Reports_Index_Prior_Check_UTC":
                prior.get("Checked_At_UTC",""),
            "Original_NSE_Annual_API_Cooldown_Until_UTC":cooldown,
            "PDF_First_Research_Priority":priority,
            "Next_Research_Action":action,
            "No_New_Source_Facts_Verified":"true",
            "Strict_Fundamental_Passes_Added":"false",
            "Trade_Eligible":"false","Orders":"DISABLED",
        })
    # Tracked current mainboard issuers with BOTH six-field newer FY
    # baselines first, then actionable already indexed/cached PDF work.
    # This is a research-efficiency sort, NOT investment ranking.
    rows.sort(key=lambda r:(
        r["Baseline_Tracked"]!="true",
        -int(r["Two_Newer_FYs_Six_Core_Row_Count_Not_Four_Year_Pass"]),
        -int(r["Two_Newer_FYs_Baseline_Row_Count"]),
        int(r["PDF_First_Research_Priority"]),
        r["Symbol"],
    ))
    write_csv_atomic(root/OUTPUT,FIELDS,rows)
    report={
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"OFFLINE_MAINBOARD_NSE_OLD_FY_PDF_FIRST_RECOVERY_QUEUE_UPDATED",
        "current_attested_nse_mainboard_eq_companies":len(rows),
        "official_nse_eq_company_years_considered":2*len(rows),
        "tracked_current_mainboard_companies":sum(
            r["Baseline_Tracked"]=="true" for r in rows),
        "both_newer_fy_six_field_research_baselines":sum(
            r["Two_Newer_FYs_Six_Core_Row_Count_Not_Four_Year_Pass"]==2
            for r in rows),
        "per_company_next_evidence_action_counts":dict(sorted(actions.items())),
        "previous_official_nse_annual_pdf_api_cooldown_until_utc":cooldown,
        "priority_companies_preview":[
            {"symbol":r["Symbol"],"action":r["Next_Research_Action"]}
            for r in rows[:10]
        ],
        "network_requests_this_run":0,
        "new_audited_xbrl_facts_verified":0,
        "pdf_prior_year_comparative_is_not_separate_original_xbrl":True,
        "strict_fundamental_passes_added":0,
        "orders":"DISABLED",
    }
    atomic_status(root/STATUS,report)
    return report


def main():
    try:
        output=snapshot(data_root())
    except (PDFRecoveryBlocked,CoverageBlocked,OfficialAnnualPdfUnavailable,
            ValueError,TypeError,OSError,csv.Error,KeyError) as exc:
        output={
            "state":"OFFLINE_PDF_FIRST_RECOVERY_QUEUE_BLOCKED",
            "reason":str(exc)[:160],
            "original_history_shortlist_and_trade_data_preserved":True,
            "orders":"DISABLED",
        }
        atomic_status(data_root()/STATUS,output)
        print(json.dumps(output,indent=2))
        return 1
    print(json.dumps(output,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
