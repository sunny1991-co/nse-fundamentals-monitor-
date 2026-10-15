#!/usr/bin/env python3
"""Read-only, standard-library diagnostic for strict NSE fundamental screening."""
import csv
from collections import Counter
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
    combinations = Counter()
    for row in report:
        reasons = [part for part in row.get("Full_Screen_Blockers", "").split("|") if part]
        blockers.update(set(reasons))
        combinations.update([tuple(sorted(set(reasons)))])
    print("\nTop blockers (company counts; overlapping):")
    for reason, count in blockers.most_common():
        print(f"{count:5}  {reason}")
    print("\nMost common blocker combinations:")
    for reasons, count in combinations.most_common(5):
        print(f"{count:5}  {'; '.join(reasons) or 'NONE'}")
    print("\nAnnual source verification summary:")
    for row in rows("annual_fundamental_quality_summary.csv"):
        print(" | ".join(f"{k}={v}" for k, v in row.items()))
    print("\nQuarterly status:")
    quarterly = rows("quarterly_change_report.csv")
    print(f"Quarterly change rows: {len(quarterly)}")
    print("\nNote: Missing or unverified data must not be treated as passing.")

if __name__ == "__main__":
    main()
