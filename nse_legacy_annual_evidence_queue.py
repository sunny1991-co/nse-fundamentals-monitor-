#!/usr/bin/env python3
"""Offline old-FY original NSE source evidence inventory, NOT a stock score.

Each current official NSE EQ mainboard issuer receives 2 FY rows:
2022-23, 2023-24. Distinguishes FY22/23 numbers printed comparatively
in the FY23/24 annual report from *independent* FY22/23 original XBRL.
No HTTP/broker calls; no verified history, backtest, shortlist or trades.
"""
from __future__ import annotations

import csv
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path

from nse_mainboard_discovery import CATALOG,write_csv_atomic
from nse_official_annual_pdf_auto_index import (
    OUTPUT as PDF_INDEX,source_pdf,
)
from nse_annual_pdf_section_review import (
    checked_sources,records,LATEST_OUTPUT,VERSION as SECTION_VERSION,
    SectionReviewBlocked,
)
from nse_annual_pdf_numeric_review import (
    OUTPUT as NUMERIC_OUTPUT,VERSION as NUMERIC_VERSION,
)
from nse_annual_pdf_prior_year_comparatives import (
    OUTPUT as PRIOR_OUTPUT,VERSION as PRIOR_VERSION,
)
from nse_annual_pdf_xbrl_crosscheck import (
    HISTORY,choose_original,decimal_value,ready_candidate,METRICS,
)
from nse_legacy_annual_recovery import audited_annual_consolidated
from nse_legacy_annual_official_auto_index import (
    LEDGER as OLD_INDEX_LEDGER,COOLDOWN,ONE_SAFE_REPROBE_DELAY,
    DIAGNOSTIC_VERSION,
)
from research_monitor.discovered_annual_universe import verified_discovered_symbols
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

OUTPUT="nse_legacy_annual_source_evidence_queue.csv"
STATUS="runtime/nse_legacy_annual_source_evidence_queue_status.json"
YEARS=("2022-23","2023-24")
FIELDS=[
    "Symbol","ISIN","Company","Financial_Year","Baseline_Tracked",
    "Research_Priority","Source_Stage","Next_Data_Action",
    "Official_PDF_Index_Links","Official_Cached_PDF_Source_Receipts",
    "Physical_Consolidated_Statement_Section_Ready",
    "PDF_Six_Core_Candidates_Not_XBRL",
    "Previous_FY_Comparative_Six_Core_From_Later_PDF",
    "Original_NSE_XBRL_Index_Links_Unverified",
    "Original_XBRL_History_Selection_State",
    "Original_XBRL_Six_Core_Fields_In_Local_History",
    "Old_NSE_Source_API_Last_State","Old_NSE_Source_API_Next_Eligible_UTC",
    "Original_Audited_XBRL_Reverified_This_Run","Strict_Annual_Pass_Added",
    "Trade_Eligible","Orders",
]


class CoverageBlocked(RuntimeError):
    pass


def read_table(path:Path,limit=60000):
    if not path.is_file():
        return []
    if path.is_symlink() or path.stat().st_size>20_000_000:
        raise CoverageBlocked("UNSAFE_OR_OVERSIZED_LOCAL_EVIDENCE_CSV")
    try:
        with path.open(encoding="utf-8-sig",newline="") as f:
            reader=csv.DictReader(f)
            if (not reader.fieldnames or
                len(reader.fieldnames)!=len(set(reader.fieldnames))):
                raise CoverageBlocked("EVIDENCE_CSV_HEADER_INVALID")
            rows=[]
            for row in reader:
                if None in row or len(rows)>=limit:
                    raise CoverageBlocked("EVIDENCE_CSV_ROW_LIMIT_OR_SCHEMA_INVALID")
                rows.append(row)
        return rows
    except (OSError,UnicodeError,csv.Error) as exc:
        raise CoverageBlocked("EVIDENCE_CSV_UNREADABLE") from exc


def bool_string(v):
    return "true" if v else "false"


def index_links(root:Path,filename:str,symbols:dict,*,pdf:bool):
    result=defaultdict(set)
    for r in read_table(root/filename):
        sym,fy=r.get("Symbol"),r.get("Financial_Year")
        if sym not in symbols or fy not in YEARS:
            continue
        if pdf:
            url=r.get("Official_Report_URL","")
            accepted=(r.get("ISIN")==symbols[sym]["ISIN"] and
                      source_pdf(url,sym) and
                      bool(r.get("Original_Metadata_SHA256","")))
        else:
            url=r.get("XBRL URL","")
            accepted=audited_annual_consolidated(
                r,f"{int('20'+fy[-2:])}-03-31")[0]
        if accepted:
            result[(sym,fy)].add(url)
    return result


def api_cooldown(ledger:dict,symbol:str,now:datetime)->tuple[str,str]:
    old=ledger.get(symbol,{})
    when=old.get("Checked_At_UTC")
    if not when:
        return "NOT_CHECKED",""
    try:
        instant=datetime.fromisoformat(when)
        if instant.tzinfo is None:
            return "TIMESTAMP_INVALID",""
        age=now-instant
        generic=(old.get("State")=="INDEX_DISCOVERY_BLOCKED" and
                 old.get("Reason")==
                     "OFFICIAL_NSE_OLD_RESULTS_NETWORK_OR_JSON_ERROR" and
                 not old.get("Diagnostic_Version"))
        delay=ONE_SAFE_REPROBE_DELAY if generic else COOLDOWN
        next_time=instant+delay
        if timedelta(0)<=age<delay:
            return old.get("Reason") or old.get("State") or "PREVIOUS_CHECK",(
                next_time.astimezone(timezone.utc).isoformat())
        return old.get("Reason") or old.get("State") or "PREVIOUS_CHECK",""
    except (ValueError,TypeError,OverflowError):
        return "TIMESTAMP_INVALID",""


def prior_comparative_valid(r:dict)->bool:
    return bool(
        r.get("Comparative_Review_Version")==PRIOR_VERSION
        and r.get("Source_Report_Financial_Year")=="2023-24"
        and r.get("Comparative_Financial_Year")=="2022-23"
        and r.get("Comparative_Fiscal_Year_End")=="2023-03-31"
        and r.get("Review_State")==
            "ALL_SIX_PRIOR_YEAR_COMPARATIVE_SOURCE_CANDIDATES_NOT_AUDIT_VERIFIED"
        and r.get("Candidate_Core_Fields")=="6"
        and r.get("Separately_Audited_Prior_Year_XBRL_Verified","").lower()=="false"
        and r.get("Strict_Fundamental_Pass","").lower()=="false"
        and r.get("Audited_Facts_Promoted")=="0"
        and r.get("Orders")=="DISABLED"
        and all(decimal_value(r.get(field)) is not None for field in METRICS)
    )


def snapshot(root:Path,*,now=None):
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    confirmed=verified_discovered_symbols(root,now=now)
    catalog=read_table(root/CATALOG,limit=5001)
    if (len(catalog)!=len(confirmed) or
        any(row.get("Symbol") not in confirmed or
            row.get("Series")!="EQ" or not row.get("ISIN")
            for row in catalog)):
        raise CoverageBlocked("CURRENT_OFFICIAL_NSE_EQ_CATALOG_NOT_ATTESTED")
    issuer={r["Symbol"]:r for r in catalog}
    if len(issuer)!=len(catalog):
        raise CoverageBlocked("OFFICIAL_MAINBOARD_DUPLICATE_SYMBOLS")

    # Receipts were checked for symbol/ISIN, original NSE PDF archive
    # source index, SHA receipt *format*, expected private path and size.
    # This inventory does NOT rehash PDF bytes or approve its numeric rows.
    checked=checked_sources(root,now)
    receipts=defaultdict(set)
    for receipt,entry,_ in checked:
        if entry["ISIN"]!=issuer[receipt["Symbol"]]["ISIN"]:
            raise CoverageBlocked("ARCHIVED_PDF_SOURCE_ISIN_CHANGED")
        receipts[(receipt["Symbol"],receipt["Financial_Year"])].add(
            (receipt["Official_Report_URL"],receipt["PDF_SHA256"]))

    pdf_links=index_links(root,PDF_INDEX,issuer,pdf=True)
    xbrl_links=defaultdict(set)
    for fy in YEARS:
        part=index_links(
            root,f"nse_legacy_financial_results_{fy}.csv",issuer,pdf=False)
        for key,links in part.items():
            xbrl_links[key].update(links)

    section=defaultdict(set)
    for row in read_table(root/LATEST_OUTPUT):
        key=(row.get("Symbol"),row.get("Financial_Year"))
        if (key[0] not in issuer or key[1] not in YEARS or
            row.get("Review_Version")!=SECTION_VERSION or
            row.get("Section_Review_State")!=
                "CANDIDATE_SECTIONS_LOCATED_SOURCE_AUDIT_STILL_REQUIRED"):
            continue
        if row.get("ISIN")!=issuer[key[0]]["ISIN"]:
            raise CoverageBlocked("PDF_SECTION_SOURCE_ISIN_CHANGED")
        proof=(row.get("Official_Report_URL"),row.get("PDF_SHA256"))
        if proof in receipts.get(key,set()):
            section[key].add(proof)

    numeric=defaultdict(set)
    for row in read_table(root/NUMERIC_OUTPUT):
        key=(row.get("Symbol"),row.get("Financial_Year"))
        if key[0] not in issuer or key[1] not in YEARS:
            continue
        proof=(row.get("Official_Report_URL"),row.get("PDF_SHA256"))
        if (row.get("ISIN")==issuer[key[0]]["ISIN"] and
            ready_candidate(row) and proof in section.get(key,set())):
            numeric[key].add(proof)

    comparative=set()
    for row in read_table(root/PRIOR_OUTPUT):
        sym=row.get("Symbol")
        key=(sym,"2023-24")
        proof=(row.get("Official_NSE_PDF_URL"),
               row.get("Official_NSE_PDF_SHA256"))
        if (sym in issuer and row.get("ISIN")==issuer[sym]["ISIN"] and
            prior_comparative_valid(row) and proof in numeric.get(key,set())):
            comparative.add(sym)

    historical=read_table(root/HISTORY)
    original_ledger={r.get("Symbol"):r for r in read_table(root/OLD_INDEX_LEDGER)
                     if r.get("Symbol") in issuer}
    rows=[]
    counts=Counter()
    for sym in sorted(issuer):
        owner=issuer[sym]
        tracked=owner.get("Baseline_Tracked")=="true"
        api_state,next_safe=api_cooldown(original_ledger,sym,now)
        for year in YEARS:
            key=(sym,year)
            original,gap=choose_original(historical,sym,year)
            has_six=bool(original and all(
                decimal_value(original.get(field)) is not None
                for field in METRICS))
            prior=(year=="2022-23" and sym in comparative)
            if has_six:
                stage,action,priority=(
                    "LOCAL_ORIGINAL_XBRL_SIX_CORE_ROW",
                    "CONTINUE_STRICT_FOUR_YEAR_VERIFICATION_NOT_AUTO_PASS",9)
            elif original:
                stage,action,priority=(
                    "LOCAL_ORIGINAL_XBRL_INCOMPLETE_CORE",
                    "REVIEW_MISSING_ORIGINAL_XBRL_FACT_CONTEXTS",0)
            elif gap=="AMBIGUOUS_XBRL_REVISIONS_OR_CONFLICTING_NUMERIC_FACTS":
                stage,action,priority=(
                    "ORIGINAL_XBRL_CONFLICTING_REVISIONS",
                    "RESOLVE_OFFICIAL_XBRL_REVISIONS_MANUALLY",0)
            elif xbrl_links.get(key):
                stage,action,priority=(
                    "ORIGINAL_XBRL_INDEX_LINKS_ONLY",
                    "RUN_INDEPENDENT_LEGACY_XBRL_XML_VERIFIER",1)
            elif numeric.get(key):
                stage,action,priority=(
                    "PDF_SIX_CORE_NUMERIC_CANDIDATES_ONLY",
                    "LOCATE_SEPARATE_ORIGINAL_AUDITED_XBRL_SOURCE",2)
            elif prior:
                stage,action,priority=(
                    "LATER_ANNUAL_PDF_PRIOR_FY_COMPARATIVE_ONLY",
                    "LOCATE_SEPARATE_PRIOR_FY_AUDITED_XBRL_SOURCE",3)
            elif section.get(key):
                stage,action,priority=(
                    "PDF_CONSOLIDATED_STATEMENT_PAGES_CANDIDATE",
                    "RUN_BOUNDED_ORIGINAL_PDF_NUMERIC_REVIEW",4)
            elif receipts.get(key):
                stage,action,priority=(
                    "ORIGINAL_PDF_CACHED_SOURCE_RECEIPT",
                    "RUN_BOUNDED_ORIGINAL_PDF_SECTION_REVIEW",5)
            elif pdf_links.get(key):
                stage,action,priority=(
                    "OFFICIAL_PDF_INDEX_LINKS_ONLY",
                    "DOWNLOAD_BOUNDED_OFFICIAL_NSE_ANNUAL_PDF",6)
            else:
                stage,action,priority=(
                    "NO_LOCAL_LEGACY_SOURCE_INDEX_YET",
                    "DISCOVER_OFFICIAL_OLDER_FY_SOURCE_INDEX_WITH_COOLDOWN",7)
            counts[stage]+=1
            rows.append({
                "Symbol":sym,"ISIN":owner["ISIN"],"Company":owner["Company"],
                "Financial_Year":year,
                "Baseline_Tracked":bool_string(tracked),
                "Research_Priority":priority,
                "Source_Stage":stage,"Next_Data_Action":action,
                "Official_PDF_Index_Links":len(pdf_links.get(key,set())),
                "Official_Cached_PDF_Source_Receipts":len(receipts.get(key,set())),
                "Physical_Consolidated_Statement_Section_Ready":
                    bool_string(bool(section.get(key))),
                "PDF_Six_Core_Candidates_Not_XBRL":
                    bool_string(bool(numeric.get(key))),
                "Previous_FY_Comparative_Six_Core_From_Later_PDF":
                    bool_string(prior),
                "Original_NSE_XBRL_Index_Links_Unverified":
                    len(xbrl_links.get(key,set())),
                "Original_XBRL_History_Selection_State":
                    "LOCAL_XBRL_RECORD_SOURCE_FLAGGED" if original else gap,
                "Original_XBRL_Six_Core_Fields_In_Local_History":
                    bool_string(has_six),
                "Old_NSE_Source_API_Last_State":api_state,
                "Old_NSE_Source_API_Next_Eligible_UTC":next_safe,
                "Original_Audited_XBRL_Reverified_This_Run":"false",
                "Strict_Annual_Pass_Added":"false",
                "Trade_Eligible":"false","Orders":"DISABLED",
            })
    # Baseline companies first, then actionable nearly-complete FY slots.
    rows.sort(key=lambda r:(
        r["Baseline_Tracked"]!="true",r["Research_Priority"],
        r["Symbol"],r["Financial_Year"]))
    write_csv_atomic(root/OUTPUT,FIELDS,rows)
    report={
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"OFFLINE_LEGACY_TWO_FY_EVIDENCE_QUEUE_UPDATED",
        "official_mainboard_eq_companies":len(issuer),
        "baseline_tracked_current_mainboard_companies":sum(
            r.get("Baseline_Tracked")=="true" for r in catalog),
        "financial_years_per_company":2,
        "company_years_in_queue":len(rows),
        "company_years_by_source_stage":dict(sorted(counts.items())),
        "source_attestation_is_from_existing_NSE_local_evidence":True,
        "old_nse_api_calls_made":0,
        "original_pdf_bytes_rehashed_this_run":0,
        "original_audited_xbrl_filings_reverified_this_run":0,
        "strict_fundamental_passes_added":0,
        "broker_orders":"DISABLED",
        "next_actions_sample":[{
            "symbol":r["Symbol"],"fy":r["Financial_Year"],
            "stage":r["Source_Stage"],"action":r["Next_Data_Action"],
        } for r in rows if r["Source_Stage"]!="LOCAL_ORIGINAL_XBRL_SIX_CORE_ROW"][:8],
    }
    atomic_status(root/STATUS,report)
    return report


def main():
    try:
        result=snapshot(data_root())
    except (CoverageBlocked,SectionReviewBlocked,
            OSError,ValueError,csv.Error) as exc:
        result={
            "state":"OFFLINE_LEGACY_EVIDENCE_QUEUE_BLOCKED",
            "reason":str(exc)[:180],
            "prior_verified_financials_and_queue_preserved":True,
            "orders":"DISABLED",
        }
        atomic_status(data_root()/STATUS,result)
        print(json.dumps(result,indent=2))
        return 1
    print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
