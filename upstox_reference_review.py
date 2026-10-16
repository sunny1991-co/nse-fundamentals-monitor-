"""Offline review queue for Upstox-vs-verified-NSE metric discrepancies.

Uses ONLY the existing local reconciliation report and saved vendor source
records. No API calls, broker tokens, orders, notifications, or edits to
audited NSE rows. Prioritization describes *source-data review urgency*,
NEVER investment merit or trade eligibility.

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_review.py
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status
from upstox_reference_batches import (
    ReferenceBatchError, _stored_paths, _existing_records,
    verified_reference_map, _source_dir, MAP_NAME,
)
from upstox_reference_reconcile import FISCAL, METRICS, VERSION as RECON_SCOPE, _number

REVIEW_SCOPE = "UPSTOX_NSE_FINANCIAL_SOURCE_REVIEW_QUEUE_V1"
MISMATCH = "DIFFERENCE_REQUIRES_SOURCE_REVIEW"
STATUSES = {
    MISMATCH, "WITHIN_TOLERANCE_VENDOR_REFERENCE",
    "VENDOR_METRIC_MISSING", "VERIFIED_NSE_METRIC_MISSING",
}
MAX_ROWS = 50000

class SourceReviewError(RuntimeError):
    pass


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError, TypeError):
        raise SourceReviewError("MISSING_OR_INVALID_RECONCILIATION_SUMMARY") from None
    if not isinstance(value, dict):
        raise SourceReviewError("MISSING_OR_INVALID_RECONCILIATION_SUMMARY")
    return value


def _validate_sources(root, recon):
    """Stop if the NSE annual or vendor instrument source changed since audit."""
    if recon.get("scope") != RECON_SCOPE:
        raise SourceReviewError("RECONCILIATION_REPORT_SCOPE_MISMATCH")
    annual = root / "annual_historical_results.csv"
    expected_nse_sha = recon.get("nse_source_file_sha256")
    if (
        annual.is_file() != bool(expected_nse_sha)
        or (annual.is_file() and _sha(annual) != expected_nse_sha)
    ):
        raise SourceReviewError("VERIFIED_NSE_SOURCE_CHANGED_RERUN_RECONCILIATION")
    mapping_path = _source_dir(root) / MAP_NAME
    if (
        not mapping_path.is_file()
        or _sha(mapping_path) != recon.get("current_vendor_nse_eq_map_sha256")
    ):
        raise SourceReviewError("VENDOR_ISIN_MAPPING_CHANGED_RERUN_RECONCILIATION")


def _load_comparisons(path, recon, records):
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            needed = {
                "symbol", "isin", "financial_year", "vendor_period",
                "metric", "vendor_crore", "verified_nse_crore",
                "difference_pct_of_larger_abs_value", "status",
            }
            if not reader.fieldnames or not needed.issubset(reader.fieldnames):
                raise SourceReviewError("COMPARISON_CSV_SCHEMA_UNVERIFIED")
            rows = []
            for row in reader:
                rows.append(row)
                if len(rows) > MAX_ROWS:
                    raise SourceReviewError("TOO_MANY_COMPARISON_ROWS")
    except (OSError, UnicodeError, csv.Error):
        raise SourceReviewError("COMPARISON_CSV_UNAVAILABLE") from None
    if len(rows) != recon.get("metric_comparisons"):
        raise SourceReviewError("RECONCILIATION_COUNT_CHANGED")
    counts = Counter()
    seen = set()
    mismatches = []
    for row in rows:
        symbol = row["symbol"]
        isin = row["isin"]
        financial_year = row["financial_year"]
        metric = row["metric"]
        status = row["status"]
        key = (symbol, financial_year, metric)
        if key in seen:
            raise SourceReviewError("DUPLICATE_COMPARISON_METRIC")
        seen.add(key)
        if (
            not isinstance(symbol,str) or symbol not in records
            or records[symbol].get("isin") != isin
            or financial_year not in FISCAL
            or row["vendor_period"] != FISCAL[financial_year][0]
            or metric not in METRICS or status not in STATUSES
        ):
            raise SourceReviewError("COMPARISON_ROW_PROVENANCE_MISMATCH")
        counts[status] += 1
        # Validate that saved vendor values are the ones reconciled; if
        # interrupted collection changed a record, insist on a fresh audit.
        vendor_key = METRICS[metric][0]
        current = (records[symbol]["financials"].get(
            row["vendor_period"]) or {}
        ).get(vendor_key)
        saved = _number(row["vendor_crore"])
        if _number(current) != saved:
            raise SourceReviewError("VENDOR_RECORD_CHANGED_RERUN_RECONCILIATION")
        if status == MISMATCH:
            percent = _number(row["difference_pct_of_larger_abs_value"])
            nse_value = _number(row["verified_nse_crore"])
            if percent is None or percent < 0 or nse_value is None or saved is None:
                raise SourceReviewError("INVALID_SOURCE_DISCREPANCY")
            mismatches.append({
                "symbol": symbol, "isin": isin, "year":financial_year,
                "metric":metric, "pct":percent,
                "vendor":saved, "nse":nse_value,
                "sign_conflict": (
                    (saved < 0 < nse_value) or (nse_value < 0 < saved)
                    or ((saved == 0) != (nse_value == 0))
                ),
            })
    expected_counts = recon.get("metric_status_counts")
    if not isinstance(expected_counts,dict):
        raise SourceReviewError("RECONCILIATION_STATUS_COUNTS_INVALID")
    for name in STATUSES:
        if counts[name] != expected_counts.get(name,0):
            raise SourceReviewError("RECONCILIATION_STATUS_COUNT_CHANGED")
    if len(set(m["symbol"] for m in mismatches)) != recon.get(
        "companies_needing_vendor_vs_nse_review"
    ):
        raise SourceReviewError("RECONCILIATION_REVIEW_COMPANIES_CHANGED")
    return mismatches


def _sector_specialist(label):
    words = str(label or "").lower()
    return any(word in words for word in (
        "bank", "finance", "nbfc", "insurance", "financial",
        "capital market", "asset management", "broking",
    ))


def create_review(root):
    root=Path(root)
    folder,record_dir,_status_path=_stored_paths(root)
    recon_dir=folder/"reconciliation"
    recon=_read_json(recon_dir/"summary.json")
    _validate_sources(root,recon)
    try:
        mapping,_digest=verified_reference_map(root)
        records=_existing_records(record_dir,mapping)
    except (ReferenceBatchError,OSError):
        raise SourceReviewError("VENDOR_REFERENCE_OR_ISIN_MAPPING_UNVERIFIED") from None
    if len(records) != recon.get("vendor_company_records_checked"):
        raise SourceReviewError("VENDOR_COMPANY_COUNT_CHANGED_RERUN_RECONCILIATION")
    comparison_file=recon_dir/"metric_comparisons.csv"
    if (
        not comparison_file.is_file()
        or _sha(comparison_file) != recon.get("metric_comparisons_csv_sha256")
    ):
        raise SourceReviewError("COMPARISON_INPUT_CHANGED_RERUN_RECONCILIATION")
    mismatches=_load_comparisons(comparison_file,recon,records)
    by_company=defaultdict(list)
    year_counts=Counter()
    metric_counts=Counter()
    for difference in mismatches:
        by_company[difference["symbol"]].append(difference)
        year_counts[difference["year"]] += 1
        metric_counts[difference["metric"]] += 1

    review_rows=[]
    priority_counts=Counter()
    specialists=0
    for symbol,problems in by_company.items():
        max_pct=max(p["pct"] for p in problems)
        sign_conflict=any(p["sign_conflict"] for p in problems)
        priority=(
            "SIGN_OR_ZERO_MISMATCH" if sign_conflict else
            "DIFFERENCE_AT_LEAST_20_PERCENT" if max_pct >= 20 else
            "DIFFERENCE_AT_LEAST_10_PERCENT" if max_pct >= 10 else
            "DIFFERENCE_OVER_5_PERCENT_OR_ABS_THRESHOLD"
        )
        sector = str(records[symbol].get("vendor_sector_reference") or "")
        specialist = _sector_specialist(sector)
        specialists += bool(specialist)
        priority_counts[priority] += 1
        metrics="; ".join(
            f'{p["year"]}:{p["metric"]}:{p["pct"]:.3f}%'
            for p in sorted(problems,key=lambda p:(p["year"],p["metric"]))
        )
        review_rows.append({
            "symbol":symbol,"isin":records[symbol]["isin"],
            "vendor_sector_reference":sector,
            "specialist_accounting_definitions_review":specialist,
            "discrepancy_cells":len(problems),
            "max_difference_pct":round(max_pct,3),
            "source_review_priority":priority,
            "difference_details":metrics,
            "strict_fundamental_pass":False,
            "trading_eligible":False,
        })

    rank={
        "SIGN_OR_ZERO_MISMATCH":0,
        "DIFFERENCE_AT_LEAST_20_PERCENT":1,
        "DIFFERENCE_AT_LEAST_10_PERCENT":2,
        "DIFFERENCE_OVER_5_PERCENT_OR_ABS_THRESHOLD":3,
    }
    review_rows.sort(key=lambda r:(
        rank[r["source_review_priority"]], -r["max_difference_pct"],
        -r["discrepancy_cells"],r["symbol"],
    ))
    summary={
        "scope":REVIEW_SCOPE,
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "vendor_companies_in_reconciliation":len(records),
        "companies_requiring_source_review":len(review_rows),
        "mismatched_financial_metrics":len(mismatches),
        "compared_numeric_metrics_within_tolerance":
            recon["metric_status_counts"].get("WITHIN_TOLERANCE_VENDOR_REFERENCE",0),
        "source_review_priorities":dict(priority_counts),
        "discrepancies_by_metric":dict(metric_counts),
        "discrepancies_by_year":dict(year_counts),
        "specialist_sector_companies_requiring_accounting_review":specialists,
        "input_comparisons_sha256":_sha(comparison_file),
        "input_verified_nse_source_sha256":recon.get("nse_source_file_sha256"),
        "input_vendor_mapping_sha256":recon.get("current_vendor_nse_eq_map_sha256"),
        "top_ten_source_review":[
            {k:r[k] for k in (
                "symbol","vendor_sector_reference","discrepancy_cells",
                "max_difference_pct","source_review_priority",
            )} for r in review_rows[:10]
        ],
        "source_data_review_only":True,
        "independent_nse_audited_verification_added":0,
        "strict_fundamental_pass_changes":0,
        "live_orders":"DISABLED",
        "warning":"Rank is urgency to review DIFFERENT accounting data, not stock performance or trading probability.",
    }
    recon_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    output=recon_dir/"company_source_review_queue.csv"
    fields=(
        "symbol","isin","vendor_sector_reference",
        "specialist_accounting_definitions_review",
        "discrepancy_cells","max_difference_pct",
        "source_review_priority","difference_details",
        "strict_fundamental_pass","trading_eligible",
    )
    with NamedTemporaryFile(
        "w",newline="",encoding="utf-8",dir=recon_dir,
        prefix=".review_",suffix=".tmp",delete=False,
    ) as f:
        tmp=Path(f.name)
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(review_rows)
    try:
        os.chmod(tmp,0o600)
        os.replace(tmp,output)
        status=recon_dir/"company_source_review_summary.json"
        atomic_status(status,summary)
        os.chmod(status,0o600)
    finally:
        tmp.unlink(missing_ok=True)
    return summary


def main():
    try:
        result=create_review(data_root())
    except (SourceReviewError,OSError,ValueError):
        raise SystemExit(
            "SOURCE_REVIEW_INPUT_STALE_OR_UNVERIFIED_RERUN_RECONCILIATION"
        ) from None
    print(json.dumps({
        k:result[k] for k in (
            "scope","vendor_companies_in_reconciliation",
            "companies_requiring_source_review","mismatched_financial_metrics",
            "compared_numeric_metrics_within_tolerance",
            "source_review_priorities","discrepancies_by_metric",
            "discrepancies_by_year",
            "specialist_sector_companies_requiring_accounting_review",
            "top_ten_source_review","strict_fundamental_pass_changes",
            "live_orders",
        )
    },indent=2))
    print("Private company review queue:",
          _stored_paths(data_root())[0]/"reconciliation"/
          "company_source_review_queue.csv")


if __name__=="__main__":
    main()
