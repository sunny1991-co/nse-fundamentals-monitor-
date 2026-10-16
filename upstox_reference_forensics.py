"""Offline forensic TRIAGE of saved Upstox-vs-NSE fundamental mismatches.

Look for plausible period/definition/sign/scale explanations before anyone
tries to repair a data source. A *hypothesis* is never an automatic correction,
audit attestation, profitable stock recommendation or order permission.

All inputs are user-local. No network, broker credentials, source mutations,
original annual file edits, strategy filters or live orders.
"""
from __future__ import annotations

from collections import Counter
import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status
from upstox_reference_batches import (
    ReferenceBatchError, _existing_records, _stored_paths, verified_reference_map,
)
from upstox_reference_reconcile import (
    FISCAL, METRICS, VERSION as RECON_SCOPE, _compare, _number, _official_history,
)
from upstox_reference_review import (
    MISMATCH, SourceReviewError, _read_json, _sha, _load_comparisons,
    _validate_sources,
)

SCOPE = "UPSTOX_NSE_DIFFERENCE_FORENSIC_TRIAGE_ONLY_V1"
ABS_CRORE_FOR_HINT = 0.1
REL_FOR_HINT = 0.01
MAX_OUTPUT = 50000
CATEGORY_ORDER = {
    "SIGN_OR_ZERO_DISAGREEMENT": 0,
    "VERY_LARGE_VALUE_DIFFERENCE": 1,
    "SUBSTANTIAL_VALUE_DIFFERENCE": 2,
    "SMALLER_REVIEW_DIFFERENCE": 3,
}


class ForensicError(RuntimeError):
    pass


def _close_for_hint(a, b):
    """Strong numerical resemblance, not a source/basis validation."""
    first, second = _number(a), _number(b)
    if first is None or second is None:
        return False
    return abs(first - second) <= max(
        ABS_CRORE_FOR_HINT,
        REL_FOR_HINT * max(abs(first), abs(second)),
    )


def _category(vendor, official, pct):
    if (
        (vendor > 0 > official) or (official > 0 > vendor)
        or ((vendor == 0) != (official == 0))
    ):
        return "SIGN_OR_ZERO_DISAGREEMENT"
    if pct >= 20:
        return "VERY_LARGE_VALUE_DIFFERENCE"
    if pct >= 10:
        return "SUBSTANTIAL_VALUE_DIFFERENCE"
    return "SMALLER_REVIEW_DIFFERENCE"


def _hypotheses(metric, year, vendor, nse, original, originals, symbol):
    """Possible explanations ONLY. Never change comparison figures."""
    notes=[]
    if _close_for_hint(vendor, -nse) and vendor * nse < 0:
        notes.append("POSSIBLE_CASH_OR_PROFIT_SIGN_CONVENTION")
    if (
        nse and (
            _close_for_hint(vendor, nse * 100)
            or _close_for_hint(vendor * 100, nse)
            or _close_for_hint(vendor, nse * 10)
            or _close_for_hint(vendor * 10, nse)
        )
    ):
        notes.append("POSSIBLE_FACTOR_OF_10_OR_100")
    if metric == "revenue":
        total_income=original.get("Total_Income_Cr")
        if _close_for_hint(vendor, total_income) and not _close_for_hint(
            nse, total_income
        ):
            notes.append("POSSIBLE_TOTAL_INCOME_VS_REVENUE_DEFINITION")
    elif metric == "net_profit":
        before_tax=original.get("Profit_Before_Tax_Cr")
        if _close_for_hint(vendor, before_tax) and not _close_for_hint(
            nse, before_tax
        ):
            notes.append("POSSIBLE_PBT_VS_NET_PROFIT_DEFINITION")
    other_year=next((fy for fy in FISCAL if fy!=year),None)
    other=originals.get((symbol,other_year)) if other_year else None
    if other is not None:
        other_value=other.get(METRICS[metric][1])
        if _close_for_hint(vendor,other_value) and not _close_for_hint(
            nse,other_value
        ):
            notes.append("POSSIBLE_ADJACENT_FISCAL_YEAR_ALIGNMENT")
    if not notes:
        notes.append("UNEXPLAINED_REQUIRES_ORIGINAL_FILING_REVIEW")
    return notes


def create_forensics(root, *, top=10, mapping_loader=verified_reference_map,
                     official_loader=_official_history):
    if isinstance(top,bool) or not isinstance(top,int) or not 1<=top<=25:
        raise ValueError("TOP_MUST_BE_1_TO_25")
    root=Path(root)
    folder, records_dir, _batch=_stored_paths(root)
    recon_dir=folder/"reconciliation"
    recon=_read_json(recon_dir/"summary.json")
    _validate_sources(root,recon)
    try:
        mapping,_=mapping_loader(root)
        records=_existing_records(records_dir,mapping)
    except (ReferenceBatchError, OSError):
        raise ForensicError("LOCAL_VENDOR_MAP_OR_REFERENCE_UNTRUSTED") from None
    if len(records)!=recon.get("vendor_company_records_checked"):
        raise ForensicError("VENDOR_RECORD_COUNT_CHANGED")
    file=recon_dir/"metric_comparisons.csv"
    if not file.is_file() or _sha(file)!=recon.get("metric_comparisons_csv_sha256"):
        raise ForensicError("COMPARISON_CSV_CHANGED")
    # Reuse existing audit's schema/count/ISIN/vendor-valued checks.
    try:
        differences=_load_comparisons(file,recon,records)
    except SourceReviewError:
        raise ForensicError("COMPARISON_RECORDS_UNVERIFIED") from None
    originals, _source_meta=official_loader(root)
    with file.open("r",encoding="utf-8-sig",newline="") as stream:
        rows=list(csv.DictReader(stream))
    if len(rows)>MAX_OUTPUT:
        raise ForensicError("COMPARISON_SIZE_LIMIT")
    differences_lookup={
        (r["symbol"],r["year"],r["metric"]) for r in differences
    }
    details=[]
    for row in rows:
        if row.get("status")!=MISMATCH:
            continue
        symbol=row["symbol"]
        fiscal=row["financial_year"]
        metric=row["metric"]
        if (symbol,fiscal,metric) not in differences_lookup:
            raise ForensicError("DISCREPANCY_NOT_IN_SOURCE_REVIEW")
        original=originals.get((symbol,fiscal))
        if not original or original.get("Source_URL")!=row.get(
            "nse_xbrl_source_url"
        ):
            raise ForensicError("ORIGINAL_NSE_XBRL_REVISION_CHANGED")
        official=_number(original.get(METRICS[metric][1]))
        vendor=_number(row["vendor_crore"])
        saved_nse=_number(row["verified_nse_crore"])
        if official is None or vendor is None or official!=saved_nse:
            raise ForensicError("ORIGINAL_NSE_NUMERIC_FACT_CHANGED")
        verified_status,absolute,pct=_compare(vendor,official)
        if (
            verified_status!=MISMATCH
            or pct!=_number(row.get("difference_pct_of_larger_abs_value"))
            or absolute!=_number(row.get("absolute_difference_crore"))
            or row.get("nse_original_filing_published_at")!=original.get(
                "Filing_Published_At"
            )
        ):
            raise ForensicError("DISCREPANCY_ARITHMETIC_UNVERIFIED")
        kind=_category(vendor,official,pct)
        details.append({
            "symbol":symbol,
            "isin":row["isin"],
            "financial_year":fiscal,
            "metric":metric,
            "upstox_vendor_crore":vendor,
            "verified_nse_crore":official,
            "difference_crore":absolute,
            "difference_pct_of_larger_abs_value":pct,
            "review_severity":kind,
            "possible_explanations_not_verified":"|".join(_hypotheses(
                metric,fiscal,vendor,official,original,originals,symbol
            )),
            "nse_filing_url":original["Source_URL"],
            "nse_original_filing_date":original["Filing_Published_At"],
            "vendor_sector_reference_only":str(
                records[symbol].get("vendor_sector_reference") or ""
            ),
            "is_verified_explanation":False,
            "eligible_for_trading":False,
        })
    if len(details)!=len(differences) or len({
        (d["symbol"],d["financial_year"],d["metric"]) for d in details
    })!=len(details):
        raise ForensicError("FORENSIC_DIFFERENCE_COUNT_MISMATCH")
    details.sort(key=lambda d:(
        CATEGORY_ORDER[d["review_severity"]],
        -d["difference_pct_of_larger_abs_value"],
        d["symbol"],d["financial_year"],d["metric"],
    ))
    level_counts=dict(Counter(d["review_severity"] for d in details))
    hypothesis_counts=dict(Counter(
        hypothesis
        for d in details
        for hypothesis in d["possible_explanations_not_verified"].split("|")
    ))
    summary={
        "scope":SCOPE,
        "created_at_utc":datetime.now(timezone.utc).isoformat(),
        "upstox_companies_checked":len(records),
        "metric_discrepancies":len(details),
        "companies_needing_review":len({r["symbol"] for r in details}),
        "source_review_severity_counts":level_counts,
        "hypothesis_counts_NOT_confirmed_causes":hypothesis_counts,
        "disagreements_by_metric":dict(Counter(d["metric"] for d in details)),
        "disagreements_by_year":dict(Counter(d["financial_year"] for d in details)),
        "top_discrepancies":[{
            k:r[k] for k in (
                "symbol","financial_year","metric","upstox_vendor_crore",
                "verified_nse_crore","difference_pct_of_larger_abs_value",
                "review_severity","possible_explanations_not_verified",
            )
        } for r in details[:top]],
        "input_comparison_sha256":_sha(file),
        "input_annual_nse_sha256":recon.get("nse_source_file_sha256"),
        "input_vendor_mapping_sha256":recon.get(
            "current_vendor_nse_eq_map_sha256"
        ),
        "automatic_source_correction":False,
        "nse_verified_years_added":0,
        "official_sector_rows_added":0,
        "strict_fundamental_pass_changes":0,
        "trading_eligibility":False,
        "live_orders":"DISABLED",
        "warning":"All hypotheses unverified: compare audited NSE XBRL tags/contexts and Upstox vendor definitions before accepting any explanation.",
    }
    recon_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=recon_dir/"source_discrepancy_forensics.csv"
    keys=(
        "symbol","isin","financial_year","metric","upstox_vendor_crore",
        "verified_nse_crore","difference_crore",
        "difference_pct_of_larger_abs_value","review_severity",
        "possible_explanations_not_verified","nse_filing_url",
        "nse_original_filing_date","vendor_sector_reference_only",
        "is_verified_explanation","eligible_for_trading",
    )
    with NamedTemporaryFile(
        "w",encoding="utf-8",newline="",dir=recon_dir,
        prefix=".forensic_",suffix=".tmp",delete=False
    ) as output:
        temp=Path(output.name)
        writer=csv.DictWriter(output,fieldnames=keys)
        writer.writeheader()
        writer.writerows(details)
    try:
        os.chmod(temp,0o600)
        os.replace(temp,target)
        atomic_status(recon_dir/"source_discrepancy_forensics_summary.json",summary)
        os.chmod(recon_dir/"source_discrepancy_forensics_summary.json",0o600)
    finally:
        temp.unlink(missing_ok=True)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top",type=int,default=10)
    args=parser.parse_args()
    try:
        result=create_forensics(data_root(),top=args.top)
    except (ForensicError,SourceReviewError,ReferenceBatchError,
            OSError,TypeError,ValueError,KeyError):
        raise SystemExit("FORENSIC_AUDIT_BLOCKED_REFRESH_SOURCE_RECONCILIATION") from None
    print(json.dumps({
        key:result[key] for key in (
            "scope","upstox_companies_checked","metric_discrepancies",
            "companies_needing_review","source_review_severity_counts",
            "hypothesis_counts_NOT_confirmed_causes","disagreements_by_metric",
            "disagreements_by_year","top_discrepancies",
            "automatic_source_correction","strict_fundamental_pass_changes",
            "live_orders",
        )
    },indent=2))
    print(
        "Private forensic report:",
        _stored_paths(data_root())[0]/"reconciliation"/
        "source_discrepancy_forensics.csv",
    )


if __name__=="__main__":
    main()
