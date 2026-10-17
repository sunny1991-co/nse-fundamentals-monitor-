"""Bounded *diagnostic only* of why original NSE FY23/FY24 XBRL fails.

100 original source reads previously yielded EXACT_APRIL_MARCH_CONTEXT_NOT_VERIFIED.
That code is a composite check: missing annual period, issuer identifiers, or
unacceptable dimension axes can all make it fail. This program disambiguates
those causes WITHOUT relaxing the source verifier or changing any fundamentals.

Single small official-archive HTTPS fetch per candidate; stops on NSE 403/429.
No alternate hosts, browser/proxy bypasses, broker APIs, or order submission.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

import nse_legacy_annual_recovery as legacy
from nse_legacy_xbrl_verify import download
import nse_annual_history as annual
from research_monitor.legacy_xbrl import MAX_XML_BYTES
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

REPORT_NAME = "legacy_xbrl_context_diagnostic.json"
SCOPE = "NSE_2022_23_2023_24_ORIGINAL_XML_CONTEXT_DIAGNOSIS_NOT_VERIFICATION"
MAX_FILES = 4
EXACT_SCHEMES = {
    "http://www.nseindia.com/NSESymbol",
    "https://www.nseindia.com/NSESymbol",
}


def local(tag):
    return str(tag).rsplit("}", 1)[-1].split(":")[-1]


def _period(node):
    """The XBRL period node only; never a reported text date or a filing URL."""
    for child in node:
        if local(child.tag) != "period":
            continue
        dates = {}
        for part in child:
            tag = local(part.tag)
            if tag in ("startDate", "endDate", "instant"):
                try:
                    dates[tag] = date.fromisoformat((part.text or "").strip())
                except ValueError:
                    dates[tag] = None
        return dates
    return {}


def _safe_scheme(scheme):
    if scheme in EXACT_SCHEMES:
        return "OFFICIAL_NSE_SYMBOL_SCHEME"
    if not scheme:
        return "EMPTY_IDENTIFIER_SCHEME"
    # NEVER include an arbitrary URI, issuer ID or XML-supplied free text.
    return "OTHER_OR_NONSTANDARD_IDENTIFIER_SCHEME"


def inspect(body, *, symbol, fiscal_end):
    """Counts only: an observed period/scheme is NOT an audited annual fact."""
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_XML_BYTES:
        raise ValueError("INVALID_XML_SIZE")
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("XML_DTD_OR_ENTITY_REJECTED")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise ValueError("INVALID_XML") from None
    if local(root.tag).lower() != "xbrl":
        raise ValueError("NOT_XBRL_INSTANCE")
    end = date.fromisoformat(fiscal_end)
    if (end.month, end.day) != (3, 31):
        raise ValueError("MARCH_FISCAL_END_REQUIRED")
    start = date(end.year-1, 4, 1)
    facts = list(root.iter())
    contexts = [node for node in facts if local(node.tag) == "context"]
    scheme_counts = Counter()
    count = Counter()
    symbol_facts = {
        (n.text or "").strip().upper()
        for n in facts if local(n.tag) == "Symbol"
    }
    basis_facts = sorted(annual.fact_basis(root))
    parsed_contexts = annual.parse_contexts(root)

    for node in contexts:
        context_id = node.attrib.get("id", "")
        dates = _period(node)
        identifiers = [
            part for part in node.iter() if local(part.tag) == "identifier"
        ]
        canonical = [
            part for part in identifiers
            if part.get("scheme") in EXACT_SCHEMES
            and (part.text or "").strip().upper() == symbol
        ]
        matching_noncanonical = [
            part for part in identifiers
            if part.get("scheme") not in EXACT_SCHEMES
            and (part.text or "").strip().upper() == symbol
        ]
        for ident in identifiers:
            scheme_counts[_safe_scheme(ident.get("scheme"))] += 1

        c = parsed_contexts.get(context_id, {})
        has_dimensions = bool(c.get("other_dimensions"))
        nonconsolidated = c.get("basis") in ("Standalone", "MIXED")
        if dates.get("startDate") is not None and dates.get("endDate") is not None:
            count["duration_contexts"] += 1
            if dates["endDate"] == end:
                count["duration_ending_at_fiscal_end"] += 1
            if dates["startDate"] == start and dates["endDate"] == end:
                count["exact_april_march_duration_contexts"] += 1
                if canonical:
                    count["exact_duration_with_matching_nse_entity"] += 1
                if matching_noncanonical:
                    count["exact_duration_with_matching_other_entity_scheme"] += 1
                if has_dimensions:
                    count["exact_duration_with_other_dimensions"] += 1
                if nonconsolidated:
                    count["exact_duration_standalone_or_mixed_dimension"] += 1
                if canonical and not has_dimensions and not nonconsolidated:
                    count["strict_duration_eligible_contexts"] += 1
        if dates.get("instant") == end:
            count["year_end_instant_contexts"] += 1
            if canonical and not has_dimensions and not nonconsolidated:
                count["strict_instant_eligible_contexts"] += 1

    for key in (
        "duration_contexts",
        "duration_ending_at_fiscal_end",
        "exact_april_march_duration_contexts",
        "exact_duration_with_matching_nse_entity",
        "exact_duration_with_matching_other_entity_scheme",
        "exact_duration_with_other_dimensions",
        "exact_duration_standalone_or_mixed_dimension",
        "strict_duration_eligible_contexts",
        "year_end_instant_contexts",
        "strict_instant_eligible_contexts",
    ):
        count.setdefault(key, 0)
    if not count["exact_april_march_duration_contexts"]:
        reason = "EXACT_ANNUAL_DURATION_MISSING"
    elif not count["exact_duration_with_matching_nse_entity"]:
        reason = "ANNUAL_DURATION_EXISTS_BUT_CANONICAL_NSE_ENTITY_MISSING"
    elif not count["strict_duration_eligible_contexts"]:
        reason = "ANNUAL_NSE_ENTITY_CONTEXT_HAS_UNVERIFIED_DIMENSIONS"
    elif not count["strict_instant_eligible_contexts"]:
        reason = "ANNUAL_CONTEXT_PRESENT_BUT_YEAR_END_INSTANT_MISSING"
    elif basis_facts != ["Consolidated"]:
        reason = "ANNUAL_CONTEXT_PRESENT_BUT_CONSOLIDATED_BASIS_NOT_ATTESTED"
    elif symbol_facts != {symbol}:
        reason = "ANNUAL_CONTEXT_PRESENT_BUT_XML_SYMBOL_FACT_NOT_ATTESTED"
    else:
        reason = "BASIC_CONTEXT_CONDITIONS_PRESENT_ADDITIONAL_AUDIT_REQUIRED"
    return {
        "reason": reason,
        "context_total": len(contexts),
        "counters": dict(count),
        "identifier_scheme_classes": dict(sorted(scheme_counts.items())),
        "xml_symbol_fact_matches": symbol_facts == {symbol},
        "xml_report_basis_facts": basis_facts,
        "verified_financial_facts": False,
    }


def diagnostic(root, *, limit=4, session=None, fetch=download, pause=None,
               now=None):
    """Read-only original XML investigation; never append annual history."""
    if not isinstance(limit, int) or not 1 <= limit <= MAX_FILES:
        raise ValueError("DIAGNOSTIC_LIMIT_1_TO_4")
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    manifest, _ = legacy.build(root)
    eligible = [
        item for item in manifest
        if item["Recovery_Status"] == "PENDING_INDEPENDENT_XBRL_REVIEW"
        and legacy.official_xbrl_link(item["Source_URL"])
    ]
    previous = {
        r.get("Source_URL"): r
        for r in legacy.csv_rows(root/"legacy_xbrl_verification.csv")
    }
    # Prefer sources already downloaded but rejected, especially the
    # repeated ambiguous context diagnostic. Interleave fiscal years.
    eligible.sort(key=lambda r: (
        0 if previous.get(r["Source_URL"], {}).get("Reason") ==
        "EXACT_APRIL_MARCH_CONTEXT_NOT_VERIFIED" else 1,
        r["Symbol"], r["Financial_Year"],
    ))
    choices = []
    for year in legacy.LEGACY_YEARS:
        group = next((x for x in eligible if x["Financial_Year"] == year), None)
        if group and group not in choices:
            choices.append(group)
    for item in eligible:
        if len(choices) >= limit:
            break
        if item not in choices:
            choices.append(item)
    choices = choices[:limit]
    stamp = now.astimezone(timezone.utc).isoformat()
    report = {
        "scope": SCOPE,
        "updated_at_utc": stamp,
        "state": "DIAGNOSTIC_COMPLETE_NOT_AUDITED",
        "max_original_xml_files": MAX_FILES,
        "requested_original_xml_files": limit,
        "selected_records": len(choices),
        "checked_records": 0,
        "context_reason_counts": {},
        "documents": [],
        "added_verified_financial_facts": 0,
        "updated_annual_history": False,
        "changed_trade_eligibility": False,
        "orders": "DISABLED",
    }
    own = session is None
    session = session or requests.Session()
    if own:
        session.headers.update({
            "User-Agent": "NSE-source-integrity-research/1.0",
            "Accept": "application/xml",
        })
    sleep = pause or (lambda _seconds: None)
    try:
        for item in choices:
            row = {
                "symbol": item["Symbol"],
                "financial_year": item["Financial_Year"],
                "index_sha256": item["Index_SHA256"],
                "state": "NOT_VERIFIED",
            }
            try:
                body = fetch(session, item["Source_URL"])
                row["source_sha256"] = hashlib.sha256(body).hexdigest()
                details = inspect(
                    body, symbol=item["Symbol"],
                    fiscal_end=item["Fiscal_Year_End"],
                )
                row.update(details)
                report["checked_records"] += 1
            except requests.HTTPError as exc:
                code = getattr(getattr(exc, "response", None), "status_code", None)
                row["reason"] = f"OFFICIAL_HTTP_{code if code is not None else 'UNKNOWN'}"
                if code in (403, 429):
                    report["state"] = "STOPPED_OFFICIAL_ACCESS_BLOCKED"
                elif code is not None and code >= 500:
                    report["state"] = "STOPPED_OFFICIAL_SERVER_ERROR"
            except (requests.RequestException, OSError, ValueError, TypeError):
                row["reason"] = "SOURCE_UNAVAILABLE_OR_MALFORMED"
            report["documents"].append(row)
            if report["state"].startswith("STOPPED"):
                break
            sleep(.1)
    finally:
        if own:
            session.close()
    counts = Counter(item.get("reason", "UNCLASSIFIED") for item in report["documents"])
    report["context_reason_counts"] = dict(sorted(counts.items()))
    atomic_status(root/"runtime"/REPORT_NAME, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-files", type=int, default=MAX_FILES)
    args = parser.parse_args()
    report = diagnostic(data_root(), limit=args.max_files)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
