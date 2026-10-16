"""Compare cached Upstox vendor financials against independently verified NSE XBRL.

Offline, read-only, vendor/reference quality control. ONLY rows with a
verified original NSE filing-index + audited consolidated XBRL source are
eligible for a cross-source comparison. Baseline CSVs alone cannot verify a
fact. Vendor periods must be March fiscal years; no December-to-March
inference. Differences are review flags, NOT proof that one provider is
incorrect, because accounting presentation may differ.

No changes to NSE original files, Upstox source data, strict screens,
portfolio, notifications or broker orders.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import csv
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status
from upstox_reference_batches import (
    ReferenceBatchError,
    _existing_records, _stored_paths, verified_reference_map,
)

VERSION = "NSE_XBRL_VS_UPSTOX_REFERENCE_RECONCILIATION_V1"
FISCAL = {
    "2024-25": ("Mar 2025", "2025-03-31"),
    "2025-26": ("Mar 2026", "2026-03-31"),
}
METRICS = {
    "revenue": ("income_revenue_vendor_crore", "Revenue_Operations_Cr"),
    "net_profit": ("income_net_profit_vendor_crore", "Net_Profit_Cr"),
    "total_assets": ("balance_total_assets_vendor_crore", "Total_Assets_Cr"),
    "operating_cash_flow": (
        "operating_cash_flow_vendor_crore", "Cash_Flow_Operating_Cr",
    ),
}
RELATIVE_TOLERANCE = 0.05
ABSOLUTE_TOLERANCE_CRORE = 0.1
MAX_RECONCILIATION_ROWS = 50000


class ReconciliationError(RuntimeError):
    pass


def _is_verified_nse_url(value):
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        return (
            url.scheme == "https"
            and url.hostname == "nsearchives.nseindia.com"
            and url.port is None and not url.username and not url.password
            and url.path.startswith("/corporate/xbrl/")
            and url.path.lower().endswith(".xml")
        )
    except ValueError:
        return False


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text.lower() in ("", "nan", "none", "na", "nat"):
        return None
    try:
        value = float(text)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _true(value):
    return str(value).strip().casefold() in ("true", "1", "yes", "verified")


def _verified(row):
    if not isinstance(row, dict):
        return False
    fy = row.get("Financial_Year", "")
    expected = FISCAL.get(fy)
    return bool(
        expected
        and row.get("Fiscal_Year_End") == expected[1]
        and row.get("Reporting_Basis", "").strip().casefold() == "consolidated"
        and row.get("Audit_Status", "").strip().casefold() == "audited"
        and _true(row.get("NSE_Consolidation_Verified", ""))
        and row.get("Status", "") in (
            "VERIFIED", "VERIFIED_WITH_MISSING_CORE_FIELDS"
        )
        and _is_verified_nse_url(row.get("Source_URL"))
        and row.get("Symbol", "").strip().upper()
    )


def _stamp(row):
    try:
        parsed = date.fromisoformat(str(row["Filing_Published_At"])[:10])
        return parsed.isoformat()
    except (KeyError,ValueError,TypeError):
        return ""


def _official_history(root):
    """Only use independently source-verified local NSE annual history.

    Ambiguous same-date, separate XBRL revisions are treated as missing.
    """
    filename = Path(root)/"annual_historical_results.csv"
    if not filename.is_file():
        return {}, {
            "nse_history_rows_seen":0,
            "verified_candidate_rows":0,
            "ambiguous_revision_symbol_years":0,
            "verified_symbol_years":0,
            "history_state":"NO_ORIGINAL_VERIFIED_NSE_HISTORY_FILE",
        }
    try:
        with filename.open("r",encoding="utf-8-sig",newline="") as file:
            reader = csv.DictReader(file)
            if not reader.fieldnames or not set(
                ["Symbol", "Financial_Year", "NSE_Consolidation_Verified",
                 "Source_URL", "Filing_Published_At",
                 "Fiscal_Year_End", "Audit_Status", "Reporting_Basis", "Status"]
            ).issubset(set(reader.fieldnames)):
                raise ReconciliationError("NSE_HISTORY_PROVENANCE_FIELDS_MISSING")
            all_rows = list(reader)
    except (OSError,UnicodeDecodeError,csv.Error):
        raise ReconciliationError("NSE_HISTORY_UNREADABLE") from None
    verified = defaultdict(list)
    for row in all_rows:
        if _verified(row):
            stamp = _stamp(row)
            if not stamp:
                continue
            symbol = row["Symbol"].strip().upper()
            verified[(symbol,row["Financial_Year"])].append(row)
    selected = {}
    ambiguous = 0
    for key,rows in verified.items():
        latest = max(_stamp(r) for r in rows)
        latest_rows=[r for r in rows if _stamp(r)==latest]
        # Two separate XBRL URLs published on the same day cannot be
        # distinguished from this CSV alone, even if one appears last.
        if len({r["Source_URL"] for r in latest_rows})!=1:
            ambiguous+=1
            continue
        # Exact duplicate index rows can be collapsed only if all comparable
        # numeric facts agree; otherwise exclude the whole symbol/year.
        def fact_key(r):
            return tuple(_number(r.get(n)) for _,n in METRICS.values())
        if len({fact_key(r) for r in latest_rows})!=1:
            ambiguous+=1
            continue
        selected[key]=latest_rows[0]
    return selected,{
        "nse_history_rows_seen":len(all_rows),
        "verified_candidate_rows":sum(map(len,verified.values())),
        "ambiguous_revision_symbol_years":ambiguous,
        "verified_symbol_years":len(selected),
        "history_state":"INDEPENDENT_NSE_INDEX_AND_XBRL_ORIGINAL_HISTORY_ONLY",
    }


def _compare(vendor_value, official_value):
    vendor=_number(vendor_value)
    official=_number(official_value)
    if vendor is None:
        return "VENDOR_METRIC_MISSING",None,None
    if official is None:
        return "VERIFIED_NSE_METRIC_MISSING",None,None
    difference=abs(vendor-official)
    divisor=max(abs(official),abs(vendor))
    percent=100*difference/divisor if divisor else 0.0
    tolerance=max(
        ABSOLUTE_TOLERANCE_CRORE,
        RELATIVE_TOLERANCE*divisor,
    )
    status=(
        "WITHIN_TOLERANCE_VENDOR_REFERENCE"
        if difference<=tolerance else "DIFFERENCE_REQUIRES_SOURCE_REVIEW"
    )
    return status,round(difference,5),round(percent,3)


def reconcile(root, *, mapping_loader=verified_reference_map,
              official_loader=_official_history, now=None):
    """Offline comparison; source metrics are never rewritten/promoted.

    A company with a discrepancy can still be a good business. No financial
    selection, trading signal, brokerage access or execution is derived.
    """
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    mapping, mapping_sha256 = mapping_loader(root)
    folder, records, _batch_status = _stored_paths(root)
    completed=_existing_records(records,mapping)
    official, source_meta=official_loader(root)
    counts=Counter()
    companies=Counter()
    comparisons=[]
    for symbol in sorted(completed):
        vendor=completed[symbol]
        years=vendor.get("financials")
        if not isinstance(years,dict):
            raise ReconciliationError("VENDOR_FINANCIALS_SCHEMA_INVALID")
        for fiscal_year,(vendor_period,_end) in FISCAL.items():
            original=official.get((symbol,fiscal_year))
            vendor_year=years.get(vendor_period) or {}
            if not isinstance(vendor_year,dict):
                raise ReconciliationError("VENDOR_PERIOD_SCHEMA_INVALID")
            if original is None:
                for label in METRICS:
                    counts["NO_INDEPENDENT_VERIFIED_NSE_YEAR"]+=1
                companies["no_nse_year"]+=1
                continue
            companies["nse_year_available"]+=1
            for name,(vkey,nkey) in METRICS.items():
                status,absolute,pct=_compare(
                    vendor_year.get(vkey), original.get(nkey)
                )
                counts[status]+=1
                comparisons.append({
                    "symbol":symbol,
                    "isin":vendor.get("isin"),
                    "financial_year":fiscal_year,
                    "vendor_period":vendor_period,
                    "metric":name,
                    "vendor_crore":_number(vendor_year.get(vkey)),
                    "verified_nse_crore":_number(original.get(nkey)),
                    "absolute_difference_crore":absolute,
                    "difference_pct_of_larger_abs_value":pct,
                    "status":status,
                    "nse_xbrl_source_url":original.get("Source_URL"),
                    "nse_original_filing_published_at":
                        original.get("Filing_Published_At"),
                })
                if status=="DIFFERENCE_REQUIRES_SOURCE_REVIEW":
                    companies["discrepancy_cells"]+=1
    if len(comparisons)>MAX_RECONCILIATION_ROWS:
        raise ReconciliationError("RECONCILIATION_SIZE_LIMIT")
    companies_with_discrepancy={
        row["symbol"] for row in comparisons
        if row["status"]=="DIFFERENCE_REQUIRES_SOURCE_REVIEW"
    }
    with_nse={
        row["symbol"] for row in comparisons
        if row["status"] in (
            "WITHIN_TOLERANCE_VENDOR_REFERENCE",
            "DIFFERENCE_REQUIRES_SOURCE_REVIEW",
        )
    }
    summary={
        "scope":VERSION,
        "generated_at_utc":now.astimezone(timezone.utc).isoformat(),
        "nse_annual_original_source":str(root/"annual_historical_results.csv"),
        "upstox_vendor_reference_source":str(records),
        "nse_source_file_sha256":(
            hashlib.sha256((root/"annual_historical_results.csv").read_bytes()).hexdigest()
            if (root/"annual_historical_results.csv").is_file() else None
        ),
        "current_vendor_nse_eq_map_sha256":mapping_sha256,
        "vendor_company_records_checked":len(completed),
        "mapped_symbols":len(mapping),
        **source_meta,
        "metric_comparisons":len(comparisons),
        "metric_status_counts":dict(sorted(counts.items())),
        "companies_with_a_comparable_nse_metric":len(with_nse),
        "companies_needing_vendor_vs_nse_review":
            len(companies_with_discrepancy),
        "comparison_tolerance_pct":5.0,
        "comparison_tolerance_minimum_crore":ABSOLUTE_TOLERANCE_CRORE,
        "sample_disagreements":[
            {"symbol":r["symbol"],"year":r["financial_year"],
             "metric":r["metric"],"difference_pct":r[
                 "difference_pct_of_larger_abs_value"
             ]}
            for r in comparisons
            if r["status"]=="DIFFERENCE_REQUIRES_SOURCE_REVIEW"
        ][:10],
        "nse_verified_years_added":0,
        "official_nse_sector_rows_added":0,
        "fundamental_strict_pass_changes":0,
        "vendor_values_promoted_to_nse":False,
        "user_trade_yes_no_enabled":False,
        "groww_orders":"DISABLED",
        "note":"Differences may reflect accounting definitions, years, statement basis, restatements or data error. Review source filings; never autocorrect.",
    }
    output=folder/"reconciliation"
    output.mkdir(parents=True,exist_ok=True,mode=0o700)
    summary_path=output/"summary.json"
    csv_path=output/"metric_comparisons.csv"
    from tempfile import NamedTemporaryFile
    with NamedTemporaryFile(
        "w",encoding="utf-8",newline="",dir=output,prefix=".recon_",
        suffix=".tmp",delete=False,
    ) as file:
        temp=Path(file.name)
        fields=(
            "symbol","isin","financial_year","vendor_period","metric",
            "vendor_crore","verified_nse_crore","absolute_difference_crore",
            "difference_pct_of_larger_abs_value","status",
            "nse_xbrl_source_url","nse_original_filing_published_at",
        )
        writer=csv.DictWriter(file,fieldnames=fields)
        writer.writeheader()
        writer.writerows(comparisons)
    try:
        os.chmod(temp,0o600)
        os.replace(temp,csv_path)
        # Tie the review CSV to this exact reconciliation run. If changed
        # between reconciliation and source triage, abort rather than ranking
        # unaudited / potentially edited values.
        summary["metric_comparisons_csv_sha256"] = hashlib.sha256(
            csv_path.read_bytes()
        ).hexdigest()
        atomic_status(summary_path,summary)
        os.chmod(summary_path,0o600)
    finally:
        temp.unlink(missing_ok=True)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-only",action="store_true",
                        help="Print comparison summary, not individual company values")
    parser.parse_args()
    try:
        report=reconcile(data_root())
    except (ReconciliationError,ReferenceBatchError,ValueError,OSError):
        # Fail closed, no broker response or private token is interpolated.
        raise SystemExit("RECONCILIATION_BLOCKED_CHECK_LOCAL_SOURCE_EVIDENCE") from None
    print(json.dumps({
        k:report[k] for k in (
            "scope", "vendor_company_records_checked","verified_symbol_years",
            "metric_comparisons","metric_status_counts",
            "companies_with_a_comparable_nse_metric",
            "companies_needing_vendor_vs_nse_review",
            "ambiguous_revision_symbol_years",
            "nse_verified_years_added","official_nse_sector_rows_added",
            "fundamental_strict_pass_changes","groww_orders",
        )
    },indent=2))
    print("Private source comparison:",_stored_paths(data_root())[0]/
          "reconciliation"/"summary.json")


if __name__=="__main__":
    main()
