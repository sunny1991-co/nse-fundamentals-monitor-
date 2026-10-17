#!/usr/bin/env python3
"""Read-only preflight: which old NSE audited-consolidated index URLs can be verified?

A CSV claiming an 'Annual' filing can still link to an XBRL containing
ONLY January-March quarter contexts. This preflight checks metadata and
prior source failures; it does NOT fetch XML or attest numeric facts.
"""
from __future__ import annotations

import csv
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path

import nse_legacy_annual_recovery as legacy
from nse_annual_history import parse_date
from nse_annual_pdf_xbrl_crosscheck import official_xml
from nse_mainboard_discovery import CATALOG,write_csv_atomic
from nse_legacy_annual_evidence_queue import read_table,CoverageBlocked
from nse_legacy_xbrl_verify import LEDGER as VERIFIER_LEDGER
from research_monitor.discovered_annual_universe import (
    verified_discovered_symbols,DiscoveredUniverseUnavailable,
)
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

OUTPUT="nse_legacy_xbrl_index_preflight.csv"
STATUS="runtime/nse_legacy_xbrl_index_preflight_status.json"
COOLDOWN=timedelta(hours=20)
SOURCE_YEARS=("2022-23","2023-24")
FIELDS=[
    "Symbol","ISIN","Financial_Year","Baseline_Tracked",
    "Metadata_Index_File","Official_XBRL_Source_URL",
    "Source_Review_State","Source_Index_Rows_Matched",
    "Official_Publication_Dates_Matched","Metadata_Ready_For_XML",
    "Independent_Audited_Consolidated_Annual_XML_Checked_This_Run",
    "Previous_Source_Attempt_State","Previous_Source_Attempt_Reason",
    "Previous_Source_Attempt_UTC","Source_Retry_Not_Before_UTC",
    "Next_Evidence_Action","Strict_Fundamental_Pass_Added","Trade_Eligible",
    "Orders",
]


class PreflightBlocked(RuntimeError):
    pass


def previous_attempt(row,now):
    if not row:
        return "","",""
    reason=row.get("Reason","")
    status=row.get("Status","")
    checked=row.get("Checked_At_UTC","")
    try:
        stamped=datetime.fromisoformat(checked)
        if stamped.tzinfo is None:
            return status,reason,"INVALID_PRIOR_ATTEMPT_TIMESTAMP"
        if timedelta(0)<=now-stamped<COOLDOWN:
            return status,reason,(stamped+COOLDOWN).astimezone(timezone.utc).isoformat()
    except (ValueError,TypeError,OverflowError):
        return status,reason,"INVALID_PRIOR_ATTEMPT_TIMESTAMP"
    return status,reason,""


def evaluate_candidate(item,*,catalog,indexes,url_claims,ledger,now):
    sym=item["Symbol"]
    fy=item["Financial_Year"]
    source=item["Source_URL"]
    r={
        "Symbol":sym,"ISIN":catalog[sym]["ISIN"],
        "Financial_Year":fy,
        "Baseline_Tracked":catalog[sym].get("Baseline_Tracked","false"),
        "Metadata_Index_File":item["Index_File"],
        "Official_XBRL_Source_URL":source,
        "Source_Review_State":"",
        "Source_Index_Rows_Matched":0,
        "Official_Publication_Dates_Matched":"",
        "Metadata_Ready_For_XML":"false",
        "Independent_Audited_Consolidated_Annual_XML_Checked_This_Run":"false",
        "Previous_Source_Attempt_State":"",
        "Previous_Source_Attempt_Reason":"",
        "Previous_Source_Attempt_UTC":"",
        "Source_Retry_Not_Before_UTC":"",
        "Next_Evidence_Action":"",
        "Strict_Fundamental_Pass_Added":"false",
        "Trade_Eligible":"false","Orders":"DISABLED",
    }
    prior=ledger.get(source,{})
    r["Previous_Source_Attempt_State"],r["Previous_Source_Attempt_Reason"],r[
        "Source_Retry_Not_Before_UTC"]=previous_attempt(prior,now)
    r["Previous_Source_Attempt_UTC"]=prior.get("Checked_At_UTC","")
    if item["Recovery_Status"]=="VERIFIED_IN_HISTORY":
        r["Source_Review_State"]="LOCAL_HISTORY_VERIFIED_FLAG_NOT_RECHECKED"
        r["Next_Evidence_Action"]="PRESERVE_PRIOR_VERIFIED_HISTORY_AND_CONTINUE_STRICT_FOUR_YEAR_REVIEW"
        return r
    if item["Recovery_Status"]=="MULTIPLE_SOURCE_REVISIONS_NEED_REVIEW":
        r["Source_Review_State"]="MULTIPLE_OFFICIAL_XBRL_REVISIONS_NEED_REVIEW"
        r["Next_Evidence_Action"]="RECONCILE_ORIGINAL_REVISIONS_BEFORE_XML_VERIFICATION"
        return r
    if item["Recovery_Status"]!="PENDING_INDEPENDENT_XBRL_REVIEW":
        r["Source_Review_State"]="NO_USABLE_OLD_ANNUAL_XBRL_INDEX_IN_TRACKED_BASELINE"
        r["Next_Evidence_Action"]="FIND_ORIGINAL_OLD_NSE_ANNUAL_INDEX_ENTRY"
        return r
    if not official_xml(source):
        r["Source_Review_State"]="OFFICIAL_XML_URL_SCHEME_HOST_OR_PATH_UNSAFE"
        r["Next_Evidence_Action"]="REVIEW_SOURCE_URL_PROVENANCE"
        return r
    if len(url_claims.get(source,set()))!=1:
        r["Source_Review_State"]="SHARED_OR_AMBIGUOUS_XML_SOURCE_URL"
        r["Next_Evidence_Action"]="REVIEW_CROSS_ISSUER_SOURCE_IDENTITY"
        return r
    expected_end=legacy.LEGACY_YEARS[fy]
    matched=[
        row for row in indexes.get(item["Index_File"],[])
        if legacy.field(row,"Symbol").upper()==sym
        and legacy.field(row,"XBRL URL")==source
        and legacy.audited_annual_consolidated(row,expected_end)[0]
    ]
    r["Source_Index_Rows_Matched"]=len(matched)
    publications={parse_date(legacy.field(row,"Broadcast Date"))
                  for row in matched}
    r["Official_Publication_Dates_Matched"]="|".join(sorted(
        p.isoformat() if p else "MISSING" for p in publications))
    if len(matched)!=1 and (not matched or len(publications)!=1):
        r["Source_Review_State"]="SOURCE_INDEX_ROW_OR_PUBLICATION_AMBIGUOUS"
        r["Next_Evidence_Action"]="REVIEW_EXACT_OLD_NSE_INDEX_ROW_AND_BROADCAST_DATE"
        return r
    if len(publications)!=1 or None in publications:
        r["Source_Review_State"]="SOURCE_INDEX_PUBLICATION_DATE_MISSING_OR_AMBIGUOUS"
        r["Next_Evidence_Action"]="RECOVER_OFFICIAL_SOURCE_BROADCAST_DATE"
        return r
    pub=next(iter(publications))
    end=parse_date(expected_end)
    if pub<end or pub>now.date():
        r["Source_Review_State"]="SOURCE_PUBLICATION_DATE_OUTSIDE_SUPPORTED_PERIOD"
        r["Next_Evidence_Action"]="REVIEW_NSE_OFFICIAL_INDEX_DATE"
        return r
    if r["Source_Retry_Not_Before_UTC"]:
        r["Source_Review_State"]="PREVIOUS_SOURCE_ATTEMPT_COOLDOWN"
        r["Next_Evidence_Action"]="RESPECT_SOURCE_COOLDOWN_AND_CHECK_STATUS_LATER"
        return r
    if prior.get("Status") in ("UNVERIFIED","VERIFIED_WITH_MISSING_CORE_FIELDS"):
        reason=str(prior.get("Reason",""))
        if reason and not reason.startswith(("SOURCE_HTTP_","SOURCE_NETWORK_ERROR")):
            r["Source_Review_State"]="EARLIER_XML_CONTEXT_OR_SCHEMA_REJECTION"
            r["Next_Evidence_Action"]="REVIEW_ORIGINAL_XML_REPORTING_CONTEXTS_NOT_AUTOMATICALLY_RETRY"
            return r
    r["Source_Review_State"]="READY_FOR_INDEPENDENT_ORIGINAL_XML_CONTEXT_REVIEW"
    r["Metadata_Ready_For_XML"]="true"
    r["Next_Evidence_Action"]="VERIFY_ORIGINAL_XML_ANNUAL_AUDIT_CONSOLIDATION_UNITS_AND_SIX_FACTS"
    return r


def snapshot(root,*,now=None):
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    verified=verified_discovered_symbols(root,now=now)
    catalog=read_table(root/CATALOG,limit=5001)
    if (len(catalog)!=len(verified) or
        len({r["Symbol"] for r in catalog})!=len(catalog)
        or any(r.get("Symbol") not in verified or
               r.get("Series")!="EQ" or not r.get("ISIN") for r in catalog)):
        raise PreflightBlocked("NSE_EQ_CURRENT_OFFICIAL_SYMBOL_ISIN_UNVERIFIED")
    owners={r["Symbol"]:r for r in catalog}
    manifest,_=legacy.build(root)
    indexes={}
    for fy in SOURCE_YEARS:
        name=f"nse_legacy_financial_results_{fy}.csv"
        indexes[name]=read_table(root/name)
    claimed=defaultdict(set)
    for item in manifest:
        if item["Symbol"] not in verified or item["Financial_Year"] not in SOURCE_YEARS:
            continue
        if item["Recovery_Status"]=="PENDING_INDEPENDENT_XBRL_REVIEW":
            claimed[item["Source_URL"]].add((item["Symbol"],item["Financial_Year"]))
    # One source URL repeated across multiple *issuer-years* requires
    # a human origin check. One URL must never verify two issuers.
    ledger_rows=read_table(root/VERIFIER_LEDGER)
    ledger={}
    for row in ledger_rows:
        url=row.get("Source_URL","")
        if not url:
            continue
        if url in ledger:
            raise PreflightBlocked("DUPLICATE_OLD_SOURCE_VERIFICATION_LEDGER_URL")
        ledger[url]=row
    rows=[]
    states=Counter()
    for item in manifest:
        if item["Symbol"] not in verified or item["Financial_Year"] not in SOURCE_YEARS:
            continue
        record=evaluate_candidate(item,catalog=owners,indexes=indexes,
                                  url_claims=claimed,ledger=ledger,now=now)
        states[record["Source_Review_State"]]+=1
        rows.append(record)
    rows.sort(key=lambda row:(
        row["Baseline_Tracked"]!="true",
        row["Metadata_Ready_For_XML"]!="true",
        row["Symbol"],row["Financial_Year"]))
    if not rows:
        raise PreflightBlocked("TRACKED_NSE_MAINBOARD_OLD_FY_INDEX_MANIFEST_EMPTY")
    write_csv_atomic(root/OUTPUT,FIELDS,rows)
    report={
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"OFFLINE_NSE_OLD_ANNUAL_XML_METADATA_PREFLIGHT_UPDATED",
        "tracked_baseline_company_years_reviewed":len(rows),
        "source_review_states":dict(sorted(states.items())),
        "metadata_ready_for_independent_original_xml_verification":states[
            "READY_FOR_INDEPENDENT_ORIGINAL_XML_CONTEXT_REVIEW"],
        "prior_source_attempts_in_cooldown":states["PREVIOUS_SOURCE_ATTEMPT_COOLDOWN"],
        "ambiguous_or_unusable_index_metadata_not_auto_promoted":len(rows)-states[
            "READY_FOR_INDEPENDENT_ORIGINAL_XML_CONTEXT_REVIEW"],
        "preflight_does_not_verify_original_xbrl_facts":True,
        "new_independently_verified_xml_facts":0,
        "original_nse_api_calls":0,
        "paid_ai_api_calls":0,
        "strict_fundamental_passes_added":0,
        "orders":"DISABLED",
        "sample_next_verified_eligible_metadata":[
            {"symbol":r["Symbol"],"financial_year":r["Financial_Year"]}
            for r in rows if r["Metadata_Ready_For_XML"]=="true"
        ][:8],
    }
    atomic_status(root/STATUS,report)
    return report


def main():
    try:
        result=snapshot(data_root())
    except (PreflightBlocked,CoverageBlocked,DiscoveredUniverseUnavailable,
            ValueError,TypeError,OSError,csv.Error) as exc:
        result={
            "state":"OLD_NSE_XBRL_PREFLIGHT_BLOCKED",
            "reason":str(exc)[:160],
            "source_verified_history_preserved":True,"orders":"DISABLED",
        }
        atomic_status(data_root()/STATUS,result)
        print(json.dumps(result,indent=2))
        return 1
    print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
