#!/usr/bin/env python3
"""Read-only audit of strict annual fundamental screening and source coverage."""
import csv
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent

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

    print("\nOfficial NSE sector classification:")
    sectors = rows("nse_sector_map.csv")
    sector_status = Counter(x.get("Lookup_Status", "UNKNOWN") for x in sectors)
    print(f"Rows: {len(sectors)}; statuses: {dict(sector_status)}")
    errors = Counter(x.get("Error", "") for x in sectors if x.get("Lookup_Status") != "OK")
    for error, count in errors.most_common(5):
        print(f"{count:5}  {error or 'UNSPECIFIED_ERROR'}")

    print("\nQuality summary:")
    for row in rows("annual_fundamental_quality_summary.csv"):
        print(" | ".join(f"{k}={v}" for k, v in row.items()))

    print("\nQuarterly change rows:", len(rows("quarterly_change_report.csv")))
    print("\nRead-only diagnostic: no files or trading signals were changed.")
    print("Never infer that a source is verified merely because core metrics are populated.")

if __name__ == "__main__":
    main()
