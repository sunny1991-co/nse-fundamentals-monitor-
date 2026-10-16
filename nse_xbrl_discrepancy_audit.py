"""Bounded original-XBRL drill-down for high-priority vendor/NSE discrepancies.

Maximum FOUR independently verified NSE-original filing URLs per invocation.
This diagnostic is source-integrity research only, NOT a replacement for
official audited-source verification, NOT a repair of any historical CSV,
NOT an investment signal, and NEVER invokes Upstox/Groww trading APIs.

The original NSE URL is taken ONLY from the user's source-hashed private
reconciliation and cross-checked against independently verified annual
history. Each raw document is parsed without external entity expansion,
and monetary amounts are considered comparable only with a proven INR unit
and exact April 1–March 31 fiscal context; conflicting facts remain a flag.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
import xml.etree.ElementTree as ET

import requests

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status
from upstox_reference_forensics import create_forensics, ForensicError
from upstox_reference_reconcile import (
    _official_history, _is_verified_nse_url, _number, METRICS,
)
from upstox_reference_batches import _stored_paths
from upstox_reference_review import SourceReviewError

SCOPE = "NSE_ORIGINAL_XBRL_DISCREPANCY_DRILLDOWN_READ_ONLY_V1"
MAX_FILES_PER_RUN = 4
MAX_XML_BYTES = 5_000_000
PAUSE_SECONDS = 1.0
ALIASES = {
    "revenue": ["RevenueFromOperations", "RevenueFromOperationsNetOfExciseDuty"],
    "net_profit": ["ProfitLossForPeriod", "ProfitLoss"],
    "total_assets": ["Assets", "TotalAssets"],
    "operating_cash_flow": [
        "NetCashFlowsFromUsedInOperatingActivities",
        "NetCashFlowsFromOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
        "CashFlowsFromOperatingActivities",
        "NetCashFlowFromOperatingActivities",
    ],
}


class DrilldownError(RuntimeError):
    pass


def _local(tag):
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _period(node):
    start=end=instant=None
    for child in node.iter():
        name=_local(child.tag).lower()
        if name not in ("startdate","enddate","instant"):
            continue
        try:
            parsed=date.fromisoformat((child.text or "").strip())
        except ValueError:
            continue
        if name=="startdate":
            start=parsed
        elif name=="enddate":
            end=parsed
        else:
            instant=parsed
    return start,end,instant


def inspect_xml(body, *, symbol, fiscal_year, official_values):
    """Show all exact-period INR aliases; never arbitrarily pick first fact.

    Values of multiple aliases/contexts are returned as possible XBRL
    facts, not merged or regarded as a single verified accounting number.
    """
    if not isinstance(body,bytes) or len(body)>MAX_XML_BYTES:
        raise DrilldownError("XML_SIZE_OR_TYPE_NOT_ALLOWED")
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise DrilldownError("XML_EXTERNAL_ENTITY_DECLARATION_REJECTED")
    try:
        xml=ET.fromstring(body)
    except (ET.ParseError,ValueError):
        raise DrilldownError("XBRL_XML_INVALID") from None
    if fiscal_year not in ("2024-25","2025-26"):
        raise DrilldownError("FISCAL_YEAR_NOT_SUPPORTED")
    start_year=int(fiscal_year[:4])
    begin=date(start_year,4,1)
    end=date(start_year+1,3,31)
    contexts={}
    ids=set()
    for node in xml.iter():
        if _local(node.tag).lower()=="context" and node.attrib.get("id"):
            cid=node.attrib["id"]
            if cid in ids:
                raise DrilldownError("DUPLICATE_XBRL_CONTEXT_ID")
            ids.add(cid)
            start,finish,instant=_period(node)
            dimensions=[]
            basis="UNSPECIFIED"
            entity=[]
            for child in node.iter():
                lower=_local(child.tag).lower()
                if lower in ("explicitmember","typedmember"):
                    axis=str(child.attrib.get("dimension","")).split(":")[-1].lower()
                    member=str(child.text or "").split(":")[-1].lower()
                    combined=axis+" "+member
                    if "standalone" in combined or "separate" in combined:
                        basis="STANDALONE"
                    elif "consolidated" in combined:
                        basis="CONSOLIDATED"
                    else:
                        dimensions.append((axis,member))
                elif lower=="identifier":
                    entity.append((child.text or "").strip().upper())
            contexts[cid]={
                "start":start,"end":finish,"instant":instant,
                "dimensions":dimensions,"basis":basis,
                "identifiers":entity,
            }
    currencies={}
    for node in xml.iter():
        if _local(node.tag).lower()!="unit" or not node.attrib.get("id"):
            continue
        names=[(child.text or "").strip() for child in node.iter()
               if _local(child.tag).lower()=="measure"]
        currencies[node.attrib["id"]]=(
            len(names)==1 and names[0].lower().endswith(":inr")
        )

    fact_bases=set()
    xml_symbols=set()
    for node in xml.iter():
        tag=_local(node.tag).lower()
        if tag in (
            "natureofreportstandaloneconsolidated", "natureofreport",
            "natureofstatement","natureoffinancialstatements",
        ):
            txt=(node.text or "").strip().lower()
            if txt in ("consolidated","standalone","separate"):
                fact_bases.add(txt)
        if tag=="symbol" and node.text:
            xml_symbols.add(node.text.strip().upper())
    basis_safe=(fact_bases=={"consolidated"})
    symbol_safe=(xml_symbols=={symbol})
    out={
        "raw_xbrl_sha256":hashlib.sha256(body).hexdigest(),
        "xml_global_consolidated_basis_present":basis_safe,
        "xml_exact_company_symbol_present":symbol_safe,
        "xml_context_count":len(contexts),
        "inr_currency_units_found":sum(currencies.values()),
        "metric_source_inspection":{},
    }
    for metric, official in official_values.items():
        if metric not in ALIASES:
            raise DrilldownError("UNRECOGNIZED_METRIC")
        match_alias={a.lower() for a in ALIASES[metric]}
        instant_metric=(metric=="total_assets")
        candidates=[]
        rejected_wrong_unit=0
        rejected_other_dimension=0
        rejected_wrong_basis=0
        for node in xml.iter():
            tag=_local(node.tag)
            if tag.lower() not in match_alias:
                continue
            ctx=contexts.get(node.attrib.get("contextRef"))
            if not ctx:
                continue
            right_period=(
                ctx["instant"]==end if instant_metric else
                ctx["start"]==begin and ctx["end"]==end
            )
            if not right_period:
                continue
            if ctx["dimensions"] or (
                ctx["identifiers"] and set(ctx["identifiers"])!={symbol}
            ):
                rejected_other_dimension+=1
                continue
            if ctx["basis"]=="STANDALONE":
                rejected_wrong_basis+=1
                continue
            unit=node.attrib.get("unitRef")
            if currencies.get(unit) is not True:
                rejected_wrong_unit+=1
                continue
            if node.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}nil")=="true":
                continue
            try:
                value=Decimal((node.text or "").strip().replace(",",""))
            except InvalidOperation:
                continue
            if not value.is_finite():
                continue
            crore=float(value/Decimal("10000000"))
            candidates.append({
                "tag":tag,"context_id":node.attrib.get("contextRef"),
                "context_basis":ctx["basis"],
                "unit_id":unit,"crore":round(crore,7),
            })
        # Duplicate numeric facts / tags are kept, rather than selecting
        # a favorable candidate. No approximate vendor-driven value choice.
        distinct={row["crore"] for row in candidates}
        matches=[r for r in candidates if _number(official) is not None
                 and abs(r["crore"]-_number(official))<=0.0001]
        uncertain_basis=any(r["context_basis"]!="CONSOLIDATED"
                            for r in candidates)
        if not basis_safe or not symbol_safe:
            verdict="DOCUMENT_REPORTING_BASIS_OR_SYMBOL_NOT_PROVEN"
        elif not candidates:
            verdict="NO_EXACT_CONTEXT_INR_FACT_FOR_METRIC"
        elif uncertain_basis:
            verdict="FACT_CONTEXT_CONSOLIDATED_BASIS_UNSPECIFIED"
        elif len(distinct)>1:
            verdict="MULTIPLE_DISTINCT_INR_FACTS_REQUIRE_TAG_REVIEW"
        elif matches:
            verdict="RAW_XBRL_FACT_AGREES_WITH_ARCHIVED_NSE_VALUE"
        else:
            verdict="ARCHIVED_NSE_VALUE_NOT_REPRODUCED_FROM_RAW_XBRL"
        out["metric_source_inspection"][metric]={
            "nse_archived_crore":_number(official),
            "candidate_inr_exact_period_count":len(candidates),
            "candidate_distinct_crore_values":sorted(distinct)[:16],
            "candidate_tags":sorted({r["tag"] for r in candidates}),
            "raw_nse_value_found_among_candidates":bool(matches),
            "unverified_context_basis_count":sum(
                r["context_basis"]!="CONSOLIDATED" for r in candidates
            ),
            "wrong_unit_rejected":rejected_wrong_unit,
            "other_dimensions_or_identifier_rejected":
                rejected_other_dimension,
            "standalone_context_rejected":rejected_wrong_basis,
            "verdict":verdict,
            "automatic_numeric_correction":False,
        }
    return out


def _download(session,url):
    if not _is_verified_nse_url(url):
        raise DrilldownError("NOT_ORIGINAL_NSE_XBRL_URL")
    try:
        with session.get(
            url, timeout=(10,30),allow_redirects=False,stream=True,
            headers={
                "User-Agent":"Mozilla/5.0 (compatible; NSE-XBRL-audit/1.0)",
                "Referer":"https://www.nseindia.com/",
                "Accept":"application/xml,text/xml,*/*",
            },
        ) as response:
            if response.status_code!=200:
                code=response.status_code
                return None,"NSE_XBRL_HTTP_"+str(code)
            pieces=[]
            length=0
            for chunk in response.iter_content(chunk_size=65536):
                if not isinstance(chunk,bytes):
                    return None,"NSE_XBRL_INVALID_RESPONSE"
                pieces.append(chunk)
                length+=len(chunk)
                if length>MAX_XML_BYTES:
                    return None,"NSE_XBRL_BYTE_LIMIT"
    except requests.RequestException:
        return None,"NSE_XBRL_NETWORK_ERROR"
    return b"".join(pieces),None


def drilldown(root, *, max_files=4, session=None, pause=time.sleep,
              triager=create_forensics, history_loader=_official_history):
    if isinstance(max_files,bool) or not isinstance(max_files,int) or not (
        1<=max_files<=MAX_FILES_PER_RUN
    ):
        raise ValueError("MAX_FILES_MUST_BE_1_TO_4")
    root=Path(root)
    # Rebuild the source-hashed offline forensic input before any network.
    forensic=triager(root)
    if forensic.get("scope")!=(
        "UPSTOX_NSE_DIFFERENCE_FORENSIC_TRIAGE_ONLY_V1"
    ):
        raise DrilldownError("SOURCE_FORENSICS_NOT_VERIFIED")
    folder,_,_=_stored_paths(root)
    forensic_csv=folder/"reconciliation"/"source_discrepancy_forensics.csv"
    original,original_summary=history_loader(root)
    with forensic_csv.open("r",encoding="utf-8-sig",newline="") as f:
        differences=list(csv.DictReader(f))
    if len(differences)!=forensic.get("metric_discrepancies"):
        raise DrilldownError("SOURCE_FORENSICS_ROW_COUNT_CHANGED")
    selection={}
    for row in differences:
        sym,fy,url=row["symbol"],row["financial_year"],row["nse_filing_url"]
        if not _is_verified_nse_url(url):
            raise DrilldownError("SOURCE_FORENSICS_NON_NSE_URL")
        official=original.get((sym,fy))
        if not official or official["Source_URL"]!=url:
            raise DrilldownError("SOURCE_FORENSICS_ORIGINAL_MISMATCH")
        metric=row["metric"]
        if metric not in METRICS or not (
            _number(official.get(METRICS[metric][1]))==
            _number(row["verified_nse_crore"])
        ):
            raise DrilldownError("SOURCE_FORENSICS_NUMERIC_MISMATCH")
        key=(sym,fy,url)
        if key not in selection and len(selection)>=max_files:
            continue
        selection.setdefault(key,{})[metric]=_number(
            official[METRICS[metric][1]]
        )
    own=session is None
    session=session or requests.Session()
    detailed=[]
    attempted=0
    blocked=False
    try:
        for (sym,fy,url),measurements in selection.items():
            body,error=_download(session,url)
            attempted+=1
            record={
                "symbol":sym,"financial_year":fy,
                "source_url":url,
                "metrics_in_review":sorted(measurements),
                "fetch_state":error or "RAW_NSE_XML_RECEIVED",
                "nse_original_source_verified_by_parser_only":True,
                "raw_xbrl_new_audited_year_added":False,
            }
            if body is not None:
                try:
                    parsed=inspect_xml(
                        body,symbol=sym,fiscal_year=fy,
                        official_values=measurements,
                    )
                    record.update(parsed)
                except DrilldownError as exc:
                    record["fetch_state"]=str(exc)
            detailed.append(record)
            if error in ("NSE_XBRL_HTTP_403","NSE_XBRL_HTTP_429"):
                blocked=True
                break
            if attempted < len(selection):
                pause(PAUSE_SECONDS)
    finally:
        if own:
            session.close()
    verdict_counts=Counter(
        d.get("verdict")
        for r in detailed
        for d in (r.get("metric_source_inspection") or {}).values()
    )
    result={
        "scope":SCOPE,
        "created_at_utc":datetime.now(timezone.utc).isoformat(),
        "previous_vendor_companies":forensic.get("upstox_companies_checked"),
        "previous_source_discrepancies":forensic.get("metric_discrepancies"),
        "selected_max_original_nse_xbrl_urls":max_files,
        "original_nse_filing_urls_attempted":attempted,
        "nse_download_blocked":blocked,
        "metric_drilldown_verdict_counts":dict(verdict_counts),
        "sample_file_reviews":detailed,
        "new_nse_verified_years_added":0,
        "original_nse_values_modified":False,
        "upstox_values_modified":False,
        "strict_fundamental_pass_changes":0,
        "trade_approvals":False,
        "live_orders":"DISABLED",
        "explanation":"Documents may have different tags/context/unit and period. Review raw taxonomies and filings before labeling a source wrong. This drilldown does not attest NSE issuer/audit eligibility.",
    }
    output=folder/"reconciliation"/"nse_xbrl_drilldown.json"
    atomic_status(output,result)
    os.chmod(output,0o600)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-files",type=int,default=4)
    args=parser.parse_args()
    try:
        result=drilldown(data_root(),max_files=args.max_files)
    except (DrilldownError,ForensicError,SourceReviewError,
            OSError,ValueError,TypeError,KeyError):
        raise SystemExit("NSE_ORIGINAL_XBRL_DRILLDOWN_BLOCKED_BY_SOURCE_CHECKS") from None
    display={
        "scope":result["scope"],
        "previous_source_discrepancies":result["previous_source_discrepancies"],
        "original_nse_filing_urls_attempted":
            result["original_nse_filing_urls_attempted"],
        "nse_download_blocked":result["nse_download_blocked"],
        "metric_drilldown_verdict_counts":
            result["metric_drilldown_verdict_counts"],
        "source_samples":[{
            "symbol":r["symbol"],"financial_year":r["financial_year"],
            "metrics_in_review":r["metrics_in_review"],
            "fetch_state":r["fetch_state"],
            "metric_source_inspection":r.get("metric_source_inspection",{}),
        } for r in result["sample_file_reviews"]],
        "original_nse_values_modified":False,
        "strict_fundamental_pass_changes":0,
        "live_orders":"DISABLED",
    }
    print(json.dumps(display,indent=2))
    print(
        "Private original filing drilldown:",
        _stored_paths(data_root())[0]/"reconciliation"/"nse_xbrl_drilldown.json",
    )


if __name__=="__main__":
    main()
