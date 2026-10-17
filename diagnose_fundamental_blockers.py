#!/usr/bin/env python3
"""Read-only audit of strict annual fundamental screening and source coverage."""
from research_monitor.paths import data_root
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = data_root()

def rows(filename):
    path = ROOT / filename
    if not path.is_file():
        print(f"MISSING FILE: {filename}")
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))

def main():
    report = rows("annual_fundamental_quality_report.csv")
    print(f"Companies in annual quality report: {len(report)}")
    blockers = Counter()
    for row in report:
        blockers.update(set(x for x in row.get("Full_Screen_Blockers", "").split("|") if x))
    print("\nAnnual screening blockers (overlapping company counts):")
    for reason, count in blockers.most_common():
        print(f"{count:5}  {reason}")

    print("\nOfficial NSE annual source verification by financial year:")
    by_year = defaultdict(Counter)
    for row in rows("annual_historical_results.csv"):
        year = row.get("Financial_Year", "")
        if not year:
            continue
        by_year[year]["rows"] += 1
        if row.get("NSE_Consolidation_Verified", "").strip().lower() in ("true", "1", "yes"):
            by_year[year]["verified"] += 1
        else:
            by_year[year]["unverified"] += 1
    for year in sorted(by_year):
        print(f"{year}: {dict(by_year[year])}")

    print("\nRequired four-year annual evidence (missing does NOT mean weak business):")
    for fiscal_year in ("2022-23", "2023-24", "2024-25", "2025-26"):
        counts = by_year[fiscal_year]
        print(f"{fiscal_year}: {counts['verified']} NSE-verified annual rows "
              f"of {counts['rows']} stored history rows")

    print("\nAnnual unverified filing reasons:")
    annual_failures = Counter()
    for row in rows("annual_historical_unverified.csv"):
        reason = row.get("Reason", "")
        if "403" in reason:
            category = "NSE XBRL HTTP 403 (access denied)"
        elif "429" in reason:
            category = "NSE XBRL HTTP 429 (rate limited)"
        elif "No unambiguous" in reason:
            category = "annual XBRL context missing"
        elif "reporting-basis" in reason or "basis is" in reason:
            category = "consolidated basis not verified"
        elif "Missing" in reason or "missing" in reason:
            category = "missing required source facts"
        else:
            category = "other: " + reason[:110]
        annual_failures[category] += 1
    for reason, count in annual_failures.most_common(10):
        print(f"{count:5}  {reason}")

    print("\nOfficial NSE sector classification:")
    sectors = rows("nse_sector_map.csv")
    sector_status = Counter(x.get("Lookup_Status", "UNKNOWN") for x in sectors)
    print(f"Rows: {len(sectors)}; statuses: {dict(sector_status)}")
    errors = Counter(x.get("Error", "") for x in sectors if x.get("Lookup_Status") != "OK")
    for error, count in errors.most_common(5):
        print(f"{count:5}  {error or 'UNSPECIFIED_ERROR'}")

    print("\nSeparate official broad-index industry reference (NOT strict sector):")
    ref = rows("nse_totalmarket_industry_reference.csv")
    strict_universe = {x.get("Symbol", "").strip().upper() for x in report}
    overlap = sum(1 for x in ref if x.get("Symbol", "").strip().upper() in strict_universe)
    try:
        status = json.loads((ROOT/"runtime"/"nse_totalmarket_industry_reference_status.json").read_text())
    except (OSError, ValueError, TypeError):
        status = {}
    print(f"Reference rows: {len(ref)}; matches annual universe: {overlap}; "
          f"download state: {status.get('state', 'NOT_YET_RUN')}")
    print("This is NOT the NSE four-tier company sector classification; "
          "strict annual and trade screens remain unchanged.")

    print("\nOriginal FY2022-23/FY2023-24 XML context root-cause diagnosis:")
    try:
        context = json.loads(
            (ROOT/"runtime"/"legacy_xbrl_context_diagnostic.json").read_text()
        )
        print("State:",context.get("state","UNKNOWN"),
              "checked original XML:",context.get("checked_records",0),
              "reasons:",context.get("context_reason_counts",{}))
    except (OSError, ValueError, TypeError):
        print("NOT_YET_RUN — this is a manual four-document source audit.")
    print("XML context diagnostics cannot modify verified annual facts.")

    print("\nQuality summary:")
    for row in rows("annual_fundamental_quality_summary.csv"):
        print(" | ".join(f"{k}={v}" for k, v in row.items()))

    raw_quarters = rows("quarterly_results.csv")
    complete_quarters = sum(
        str(row.get("Fundamentals_Complete", "")).lower() in ("true", "yes", "1")
        for row in raw_quarters
    )
    print(f"\nRaw verified quarterly rows: {len(raw_quarters)}; complete core facts: {complete_quarters}")
    print("Quarterly processed statuses:",
          dict(Counter(row.get("Status", "UNKNOWN") for row in rows("quarterly_processed_filings.csv"))))
    print("\nQuarterly change rows:", len(rows("quarterly_change_report.csv")))
    print("\nRead-only diagnostic: no files or trading signals were changed.")
    print("Never infer that a source is verified merely because core metrics are populated.")

if __name__ == "__main__":
    main()

