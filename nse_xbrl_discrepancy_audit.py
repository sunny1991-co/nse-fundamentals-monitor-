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
from urllib.parse import urlsplit
from datetime import date, datetime, timezone
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
import re
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


def _issuer_scheme_label(scheme):
    """Bound only public XBRL scheme metadata; never disclose identifiers."""
    if not isinstance(scheme,str):
        return "UNSPECIFIED"
    value=scheme.strip().lower()
    if not value:
        return "UNSPECIFIED"
    try:
        uri=urlsplit(value)
        host=(uri.hostname or "").lower()
        if uri.scheme not in ("http","https") or not host:
            return "UNKNOWN_SCHEME_FORMAT"
        if host=="www.nseindia.com" and uri.path.lower()=="/nsesymbol":
            return "NSE_SYMBOL_URI"
        if host in ("www.mca.gov.in","mca.gov.in") and uri.path.lower()=="/cin":
            return "MCA_CIN_URI"
        if host in ("www.sebi.gov.in","sebi.gov.in"):
            return "SEBI_SCHEME_URI_NOT_INDEPENDENT_ISSUER_PROOF"
        if host in ("www.bseindia.com","bseindia.com"):
            return "BSE_SCHEME_URI_NOT_INDEPENDENT_ISSUER_PROOF"
        # Display domain/type only; never leak arbitrary XML scheme strings.
        return "OTHER_URI_DOMAIN:"+host[:80]
    except ValueError:
        return "UNKNOWN_SCHEME_FORMAT"


def _identifier_format_hint(value):
    """Report *shape only*, never expose the identifier or claim validity."""
    if not isinstance(value,str):
        return "EMPTY_OR_UNSUPPORTED"
    value=value.strip().upper()
    if not value or len(value)>96:
        return "EMPTY_OR_UNSUPPORTED"
    if re.fullmatch(r"[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}",value):
        return "CIN_SHAPED_UNVERIFIED"
    if re.fullmatch(r"IN[A-Z0-9]{10}",value):
        return "ISIN_SHAPED_UNVERIFIED"
    if re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]",value):
        return "PAN_SHAPED_UNVERIFIED"
    if re.fullmatch(r"[0-9]{5,8}",value):
        return "NUMERIC_EXCHANGE_CODE_SHAPED_UNVERIFIED"
    if re.fullmatch(r"[A-Z][A-Z0-9_.&-]{0,39}",value):
        return "SYMBOL_OR_TEXT_SHAPED_UNVERIFIED"
    return "OTHER_UNVERIFIED"


def _document_cin_claims(xml):
    """CIN values claimed by source XML, not independently verified issuer IDs."""
    claimed=set()
    for node in xml.iter():
        if _local(node.tag).lower() not in (
            "corporateidentitynumber",
            "corporateidentificationnumber",
            "cin",
        ):
            continue
        value=(node.text or "").strip().upper()
        if value and len(value)<=64:
            claimed.add(value)
    return claimed


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


def inspect_xml(body, *, symbol, fiscal_year, official_values,
                archived_context_ids=None):
    """Show all exact-period INR aliases; never arbitrarily pick first fact.

    Values of multiple aliases/contexts are returned as possible XBRL
    facts, not merged or regarded as a single verified accounting number.
    """
    if not isinstance(body,bytes) or len(body)>MAX_XML_BYTES:
        raise DrilldownError("XML_SIZE_OR_TYPE_NOT_ALLOWED")
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise DrilldownError("XML_EXTERNAL_ENTITY_DECLARATION_REJECTED")
    try:
        # QName prefix text in XBRL units must resolve to the official
        # iso4217 namespace; arbitrary "anything:INR" is not accepted.
        namespaces={}
        for _, (prefix, uri) in ET.iterparse(
            io.BytesIO(body), events=("start-ns",)
        ):
            if prefix in namespaces and namespaces[prefix]!=uri:
                raise DrilldownError("AMBIGUOUS_XML_NAMESPACE_REDEFINITION")
            namespaces[prefix]=uri
        xml=ET.fromstring(body)
    except (ET.ParseError,ValueError):
        raise DrilldownError("XBRL_XML_INVALID") from None
    if archived_context_ids is None:
        archived_context_by_metric={}
    elif isinstance(archived_context_ids,dict):
        archived_context_by_metric={
            metric:{
                str(item) for item in ids
                if isinstance(item,str) and 0<len(item)<=128
            }
            for metric, ids in archived_context_ids.items()
            if metric in ALIASES and isinstance(ids,(set,list,tuple))
        }
    elif isinstance(archived_context_ids,(set,list,tuple)):
        # Existing test/API support only. For multi-metric audit runs the
        # calling drilldown passes a distinct archived context per metric.
        common={
            str(item) for item in archived_context_ids
            if isinstance(item,str) and 0<len(item)<=128
        }
        archived_context_by_metric={m:common for m in official_values}
    else:
        raise DrilldownError("INVALID_ARCHIVED_CONTEXT_ID_INPUT")
    if fiscal_year not in ("2024-25","2025-26"):
        raise DrilldownError("FISCAL_YEAR_NOT_SUPPORTED")
    start_year=int(fiscal_year[:4])
    begin=date(start_year,4,1)
    end=date(start_year+1,3,31)
    document_cins=_document_cin_claims(xml)
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
            stated_bases=set()
            entity=[]
            for child in node.iter():
                lower=_local(child.tag).lower()
                if lower in ("explicitmember","typedmember"):
                    axis=str(child.attrib.get("dimension","")).split(":")[-1].lower()
                    member=str(child.text or "").split(":")[-1].lower()
                    typed=(lower=="typedmember")
                    if typed:
                        # Typed dimensions must never be silently counted as
                        # a consolidated reporting-basis declaration.
                        dimensions.append((axis,"TYPED_DIMENSION"))
                    elif "standalone" in member or "separate" in member:
                        stated_bases.add("STANDALONE")
                    elif "consolidated" in member:
                        stated_bases.add("CONSOLIDATED")
                    else:
                        dimensions.append((axis,member))
                elif lower=="identifier":
                    entity.append((
                        (child.text or "").strip().upper(),
                        str(child.attrib.get("scheme","")).strip().lower(),
                    ))
            basis=(
                "CONFLICTING" if len(stated_bases)>1 else
                next(iter(stated_bases)) if stated_bases else "UNSPECIFIED"
            )
            nse_scheme_ids={
                v for v,scheme in entity
                if scheme in (
                    "http://www.nseindia.com/nsesymbol",
                    "https://www.nseindia.com/nsesymbol",
                )
            }
            identifier_status=(
                "NSE_SYMBOL_MISMATCH" if nse_scheme_ids and nse_scheme_ids!={symbol}
                else "NSE_SYMBOL_MATCHED" if nse_scheme_ids=={symbol}
                else "IDENTIFIER_SCHEME_NOT_NSE_SYMBOL_VERIFIED"
            )
            scheme_labels=sorted({
                _issuer_scheme_label(scheme) for _value,scheme in entity
            }) or ["UNSPECIFIED"]
            value_shape_labels=sorted({
                _identifier_format_hint(identifier)
                for identifier,_scheme in entity
            }) or ["EMPTY_OR_UNSUPPORTED"]
            id_equal_symbol=(len(entity)==1 and entity[0][0]==symbol)
            # Matching CIN values within the *same XML file* is weak issuer
            # evidence, not an independent NSE registry attestation. Thus it
            # intentionally does NOT change identifier_status eligibility.
            cin_ids={
                v for v,scheme in entity
                if _issuer_scheme_label(scheme)=="MCA_CIN_URI"
            }
            if cin_ids:
                cin_relation=(
                    "DOCUMENT_CIN_FIELD_AMBIGUOUS"
                    if len(document_cins)>1 else
                    "DOCUMENT_CIN_CONTEXT_MATCH_UNVERIFIED_EXTERNAL"
                    if cin_ids==document_cins and len(document_cins)==1
                    else "DOCUMENT_CIN_CONTEXT_CONFLICTS_WITH_XML_CLAIM"
                    if document_cins else
                    "DOCUMENT_CIN_FACT_NOT_PRESENT"
                )
            elif identifier_status=="NSE_SYMBOL_MATCHED":
                cin_relation="NSE_SYMBOL_CONTEXT_SCHEME_PRESENT"
            else:
                cin_relation="NOT_A_RECOGNIZED_CIN_CONTEXT"
            contexts[cid]={
                "start":start,"end":finish,"instant":instant,
                "dimensions":dimensions,"basis":basis,
                "identifiers":entity,
                "identifier_status":identifier_status,
                "identifier_scheme_labels":scheme_labels,
                "identifier_value_shapes_not_verified":value_shape_labels,
                "identifier_equals_document_symbol":id_equal_symbol,
                "document_cin_relation":cin_relation,
            }
    currencies={}
    for node in xml.iter():
        if _local(node.tag).lower()!="unit" or not node.attrib.get("id"):
            continue
        # A currency unit must contain exactly one direct <measure>; ratio
        # and compound unit definitions are NOT monetary INR amounts.
        names=[(child.text or "").strip()
               for child in node if _local(child.tag).lower()=="measure"]
        other_parts=[child for child in node
                     if _local(child.tag).lower()!="measure"]
        prefix,separator,unit_name=names[0].partition(":") if len(names)==1 else ("","","")
        currencies[node.attrib["id"]]=(
            len(names)==1 and not other_parts and separator==":"
            and unit_name=="INR"
            and namespaces.get(prefix)=="http://www.xbrl.org/2003/iso4217"
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
    issuer_tuples={tuple(ctx["identifiers"]) for ctx in contexts.values()}
    unique_entity_identifiers={
        item for ctx in contexts.values() for item in ctx["identifiers"]
    }
    common_context_issuer=(
        len(issuer_tuples)==1 and len(unique_entity_identifiers)==1
        and len(contexts)>0
    )
    out={
        "raw_xbrl_sha256":hashlib.sha256(body).hexdigest(),
        "xml_global_consolidated_basis_present":basis_safe,
        "xml_exact_company_symbol_present":symbol_safe,
        "xml_context_count":len(contexts),
        "inr_currency_units_found":sum(currencies.values()),
        "xml_claimed_cin_count":len(document_cins),
        "unverified_distinct_context_issuer_identifiers_count":
            len(unique_entity_identifiers),
        "all_xbrl_contexts_use_same_self_declared_issuer":
            common_context_issuer,
        "context_identifier_value_shape_classes":dict(Counter(
            shape
            for ctx in contexts.values()
            for shape in ctx["identifier_value_shapes_not_verified"]
        )),
        "context_identifiers_equal_filing_symbol_count":sum(
            ctx["identifier_equals_document_symbol"]
            for ctx in contexts.values()
        ),
        "issuer_registration_scheme_is_not_independent_identity_proof":True,
        "context_identifier_scheme_classes":dict(Counter(
            label
            for ctx in contexts.values()
            for label in ctx["identifier_scheme_labels"]
        )),
        "legacy_parser_issuer_identity_gap_possible":False,
        "metric_source_inspection":{},
    }
    for metric, official in official_values.items():
        if metric not in ALIASES:
            raise DrilldownError("UNRECOGNIZED_METRIC")
        archived_context_ids=archived_context_by_metric.get(metric,set())
        match_alias={a.lower() for a in ALIASES[metric]}
        instant_metric=(metric=="total_assets")
        candidates=[]
        rejected_wrong_unit=0
        rejected_other_dimension=0
        rejected_identifier=0
        rejected_wrong_basis=0
        rejected_samples=[]
        rejected_matching_archived=[]
        rejected_matching_original=[]
        identity_evidence_counts=Counter()
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
            unit=node.attrib.get("unitRef")
            if node.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}nil") in ("true","1"):
                continue
            try:
                value=Decimal((node.text or "").strip().replace(",",""))
            except InvalidOperation:
                continue
            if not value.is_finite():
                continue
            if currencies.get(unit) is not True or "scale" in node.attrib:
                rejected_wrong_unit+=1
                continue
            crore=round(float(value/Decimal("10000000")),7)
            reason=None
            if ctx["dimensions"]:
                reason="DIMENSIONED_CONTEXT"
                rejected_other_dimension+=1
            elif ctx["identifier_status"]=="NSE_SYMBOL_MISMATCH":
                reason="NSE_SYMBOL_IDENTIFIER_MISMATCH"
                rejected_identifier+=1
            elif ctx["basis"] in ("STANDALONE","CONFLICTING"):
                reason="STANDALONE_OR_CONFLICTING_BASIS"
                rejected_wrong_basis+=1
            elif ctx["identifier_status"]!="NSE_SYMBOL_MATCHED":
                reason="CONTEXT_IDENTIFIER_SCHEME_UNVERIFIED"
                rejected_identifier+=1
            if reason:
                identity_evidence_counts[ctx["document_cin_relation"]]+=1
                if _number(official) is not None and abs(crore-_number(official))<=0.0001:
                    rejected_matching_original.append(reason)
                    if node.attrib.get("contextRef") in archived_context_ids:
                        rejected_matching_archived.append(reason)
                if len(rejected_samples)<4:
                    rejected_samples.append({
                        "tag":tag,
                        "context_id":node.attrib.get("contextRef"),
                        "crore":crore,
                        "reason":reason,
                        "basis":ctx["basis"],
                        "identifier_status":ctx["identifier_status"],
                        "identifier_scheme_classes":
                            ctx["identifier_scheme_labels"],
                        "issuer_identifier_value_shape_not_verified":
                            ctx["identifier_value_shapes_not_verified"],
                        "issuer_identifier_equals_filing_symbol":
                            ctx["identifier_equals_document_symbol"],
                        "same_self_declared_issuer_across_all_contexts":
                            common_context_issuer,
                        "document_cin_relation":
                            ctx["document_cin_relation"],
                        "dimension_names":[d[0][:80] for d in ctx["dimensions"][:4]],
                        "in_archived_context_ids":
                            node.attrib.get("contextRef") in archived_context_ids,
                    })
                continue
            candidates.append({
                "tag":tag,"context_id":node.attrib.get("contextRef"),
                "context_basis":ctx["basis"],
                "unit_id":unit,"crore":crore,
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
        elif not candidates and rejected_matching_archived:
            verdict="ARCHIVED_NSE_VALUE_IN_EXCLUDED_CONTEXT_REVIEW"
        elif not candidates and rejected_matching_original:
            verdict="RAW_NSE_VALUE_ONLY_IN_EXCLUDED_CONTEXT_REVIEW"
        elif not candidates and rejected_samples:
            verdict="EXCLUDED_XBRL_CONTEXT_FACT_REQUIRES_REVIEW"
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
                rejected_other_dimension+rejected_identifier,
            "non_basis_dimension_rejected":rejected_other_dimension,
            "identifier_scheme_or_symbol_rejected":rejected_identifier,
            "standalone_context_rejected":rejected_wrong_basis,
            "archived_selected_context_ids_count":len(archived_context_ids),
            "excluded_facts_matching_archived_nse_value":
                len(rejected_matching_original),
            "excluded_matching_facts_in_archived_context_ids":
                len(rejected_matching_archived),
            "excluded_fact_examples_not_verified":rejected_samples,
            "excluded_context_identity_evidence_counts":
                dict(identity_evidence_counts),
            "legacy_parser_may_have_selected_context_without_independent_issuer_scheme_check":
                bool(rejected_matching_archived),
            "verdict":verdict,
            "automatic_numeric_correction":False,
        }
    out["legacy_parser_issuer_identity_gap_possible"]=any(
        r["legacy_parser_may_have_selected_context_without_independent_issuer_scheme_check"]
        for r in out["metric_source_inspection"].values()
    )
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
        group=selection.setdefault(key,{
            "values":{},
            "archived_context_ids_by_metric":{},
        })
        group["values"][metric]=_number(official[METRICS[metric][1]])
        context_field=(
            "Instant_Context_IDs" if metric=="total_assets"
            else "Annual_Context_IDs"
        )
        group["archived_context_ids_by_metric"][metric]={
            x for x in str(official.get(context_field) or "").split("|")
            if x and len(x)<=128
        }
    own=session is None
    session=session or requests.Session()
    detailed=[]
    attempted=0
    blocked=False
    stop_reason=None
    try:
        for (sym,fy,url),group in selection.items():
            measurements=group["values"]
            body,error=_download(session,url)
            attempted+=1
            record={
                "symbol":sym,"financial_year":fy,
                "source_url":url,
                "metrics_in_review":sorted(measurements),
                "fetch_state":error or "RAW_NSE_XML_RECEIVED",
                "nse_original_url_provenance_accepted":True,
                "issuer_identity_independently_attested":False,
                "raw_xbrl_new_audited_year_added":False,
            }
            if body is not None:
                try:
                    parsed=inspect_xml(
                        body,symbol=sym,fiscal_year=fy,
                        official_values=measurements,
                        archived_context_ids=group[
                            "archived_context_ids_by_metric"
                        ],
                    )
                    record.update(parsed)
                except DrilldownError as exc:
                    record["fetch_state"]=str(exc)
            detailed.append(record)
            if error in ("NSE_XBRL_HTTP_403","NSE_XBRL_HTTP_429"):
                blocked=True
                stop_reason=error
                break
            if error and (
                error in ("NSE_XBRL_NETWORK_ERROR","NSE_XBRL_INVALID_RESPONSE",
                          "NSE_XBRL_BYTE_LIMIT")
                or error.startswith("NSE_XBRL_HTTP_5")
            ):
                # Do not overwhelm a temporarily blocked/offline archive.
                stop_reason=error
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
    issuer_identity_pending=sum(
        bool(d.get(
            "legacy_parser_may_have_selected_context_without_independent_issuer_scheme_check"
        ))
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
        "stop_reason":stop_reason,
        "metric_drilldown_verdict_counts":dict(verdict_counts),
        "issuer_identity_attestation_pending_metric_cells":issuer_identity_pending,
        "issuer_identity_independently_attested":False,
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
        "stop_reason":result["stop_reason"],
        "metric_drilldown_verdict_counts":
            result["metric_drilldown_verdict_counts"],
        "issuer_identity_attestation_pending_metric_cells":
            result["issuer_identity_attestation_pending_metric_cells"],
        "source_samples":[{
            "symbol":r["symbol"],"financial_year":r["financial_year"],
            "metrics_in_review":r["metrics_in_review"],
            "fetch_state":r["fetch_state"],
            "xml_context_identifier_scheme_classes":
                r.get("context_identifier_scheme_classes",{}),
            "xml_context_identifier_value_shape_classes":
                r.get("context_identifier_value_shape_classes",{}),
            "xml_claimed_cin_fact_count":r.get("xml_claimed_cin_count"),
            "xbrl_contexts_use_same_self_declared_issuer":
                r.get("all_xbrl_contexts_use_same_self_declared_issuer"),
            "xml_distinct_context_issuer_identifier_count":
                r.get("unverified_distinct_context_issuer_identifiers_count"),
            "legacy_parser_issuer_identity_gap_possible":
                r.get("legacy_parser_issuer_identity_gap_possible",False),
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
