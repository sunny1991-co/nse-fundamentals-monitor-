"""Fast provisional two-year fundamental screen.

This deliberately relaxes the project's strict four-year/sector/FCF screen at the
user's request for a quicker, lower-confidence research path. It never replaces
or overwrites the strict shortlist and must not authorize live alerts or orders.
Only symbols whose two-year annual data quality is already PASS are considered.
"""
from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
import math
import pandas as pd

from research_monitor.paths import data_root

ROOT = data_root()
QUALITY = ROOT / "annual_fundamental_quality_report.csv"
FY24 = ROOT / "FY2425.csv"
FY25 = ROOT / "NSE_FY2025-26_Consolidated_Fundamentals.csv"
OUTPUT = ROOT / "fast_fundamental_shortlist.csv"
SUMMARY = ROOT / "fast_fundamental_screen_summary.csv"

def source_sha256(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def source_hashes() -> dict[str, str]:
    return {
        "Annual_Quality_SHA256": source_sha256(QUALITY),
        "FY2024_25_SHA256": source_sha256(FY24),
        "FY2025_26_SHA256": source_sha256(FY25),
        "Fast_Shortlist_SHA256": source_sha256(OUTPUT),
    }


CORE = [
    "Revenue_Operations_Cr",
    "Net_Profit_Cr",
    "Basic_EPS_Rs",
    "Total_Assets_Cr",
    "Total_Equity_Cr",
    "Total_Borrowings_Cr",
]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    frame.columns = [str(c).strip().lstrip("\ufeff") for c in frame.columns]
    return frame


def num(value):
    try:
        n = float(str(value).replace(",", "").strip())
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def growth(old, new):
    if old is None or new is None or old <= 0:
        return None
    return (new / old - 1.0) * 100.0


def audited_consolidated(row: pd.Series) -> bool:
    text = str(row.get("Reporting_Type", "")).strip().lower()
    return "audited" in text and "consolidated" in text and str(row.get("Status", "")).strip().upper() == "OK"


def latest_unique(frame: pd.DataFrame) -> dict[str, pd.Series]:
    if frame.empty or "Symbol" not in frame:
        return {}
    frame = frame.copy()
    frame["Symbol"] = frame["Symbol"].astype(str).str.strip().str.upper()
    frame = frame[frame["Symbol"].ne("")]
    return {sym: group.iloc[-1] for sym, group in frame.groupby("Symbol", sort=False)}


def main() -> None:
    quality = read_csv(QUALITY)
    fy24 = read_csv(FY24)
    fy25 = read_csv(FY25)

    columns = [
        "Symbol", "Company", "Fast_Fundamental_Pass",
        "Revenue_Growth_Pct", "Net_Profit_Growth_Pct", "EPS_Growth_Pct",
        "Prior_Net_Profit_Margin_Pct", "Latest_Net_Profit_Margin_Pct",
        "Latest_Debt_Equity", "Failure_Reasons", "Research_Scope",
    ]

    if quality.empty or "Two_Year_Baseline_Data_Quality" not in quality or "Symbol" not in quality:
        pd.DataFrame(columns=columns).to_csv(OUTPUT, index=False)
        pd.DataFrame([{
            "Report_Generated_On": date.today().isoformat(),
            "Verified_Two_Year_Universe": 0,
            "Fast_Fundamental_Pass_Count": 0,
            "Status": "WAITING_FOR_VERIFIED_TWO_YEAR_ANNUAL_QUALITY",
            "Scope": "PROVISIONAL_LOWER_CONFIDENCE_RESEARCH_ONLY",
            **source_hashes(),
        }]).to_csv(SUMMARY, index=False)
        print("Fast fundamental screen waiting for verified two-year annual quality.")
        return

    quality = quality.copy()
    quality["Symbol"] = quality["Symbol"].astype(str).str.strip().str.upper()
    verified = set(
        quality.loc[
            quality["Two_Year_Baseline_Data_Quality"].astype(str).str.upper().eq("PASS"),
            "Symbol",
        ]
    )
    a24, a25 = latest_unique(fy24), latest_unique(fy25)
    rows = []

    for symbol in sorted(verified):
        p, n = a24.get(symbol), a25.get(symbol)
        blockers = []
        if p is None or n is None:
            blockers.append("BOTH_ANNUAL_BASELINES_REQUIRED")
            continue
        if not audited_consolidated(p) or not audited_consolidated(n):
            blockers.append("AUDITED_CONSOLIDATED_OK_REQUIRED")

        vals24 = {field: num(p.get(field)) for field in CORE}
        vals25 = {field: num(n.get(field)) for field in CORE}
        if any(vals24[field] is None or vals25[field] is None for field in CORE):
            blockers.append("SIX_CORE_FIELDS_REQUIRED_BOTH_YEARS")

        rev_growth = growth(vals24["Revenue_Operations_Cr"], vals25["Revenue_Operations_Cr"])
        profit_growth = growth(vals24["Net_Profit_Cr"], vals25["Net_Profit_Cr"])
        eps_growth = growth(vals24["Basic_EPS_Rs"], vals25["Basic_EPS_Rs"])

        for name, value in (
            ("REVENUE", rev_growth),
            ("NET_PROFIT", profit_growth),
            ("EPS", eps_growth),
        ):
            if value is None or value < 0:
                blockers.append(f"{name}_TWO_YEAR_TREND_NOT_POSITIVE")

        rev24, rev25 = vals24["Revenue_Operations_Cr"], vals25["Revenue_Operations_Cr"]
        np24, np25 = vals24["Net_Profit_Cr"], vals25["Net_Profit_Cr"]
        margin24 = np24 / rev24 * 100.0 if rev24 and np24 is not None and rev24 > 0 else None
        margin25 = np25 / rev25 * 100.0 if rev25 and np25 is not None and rev25 > 0 else None
        if margin24 is None or margin25 is None or margin24 <= 0 or margin25 <= 0:
            blockers.append("POSITIVE_NET_MARGIN_REQUIRED_BOTH_YEARS")
        elif margin25 < margin24 - 3.0:
            blockers.append("NET_MARGIN_DECLINE_OVER_3_PCT_POINTS")

        equity = vals25["Total_Equity_Cr"]
        debt = vals25["Total_Borrowings_Cr"]
        debt_equity = debt / equity if debt is not None and equity is not None and equity > 0 else None
        if equity is None or equity <= 0:
            blockers.append("POSITIVE_LATEST_EQUITY_REQUIRED")
        if debt_equity is None or debt_equity > 1.0:
            blockers.append("LATEST_DEBT_EQUITY_MUST_BE_AT_MOST_1")

        if vals25["Total_Assets_Cr"] is None or vals25["Total_Assets_Cr"] <= 0:
            blockers.append("POSITIVE_LATEST_ASSETS_REQUIRED")

        rows.append({
            "Symbol": symbol,
            "Company": str(n.get("Company", "") or p.get("Company", "")),
            "Fast_Fundamental_Pass": not blockers,
            "Revenue_Growth_Pct": rev_growth,
            "Net_Profit_Growth_Pct": profit_growth,
            "EPS_Growth_Pct": eps_growth,
            "Prior_Net_Profit_Margin_Pct": margin24,
            "Latest_Net_Profit_Margin_Pct": margin25,
            "Latest_Debt_Equity": debt_equity,
            "Failure_Reasons": "|".join(dict.fromkeys(blockers)),
            "Research_Scope": "PROVISIONAL_LOWER_CONFIDENCE_RESEARCH_ONLY",
        })

    report = pd.DataFrame(rows, columns=columns)
    passed = report[report["Fast_Fundamental_Pass"].astype(bool)].copy() if not report.empty else report
    passed.to_csv(OUTPUT, index=False, na_rep="")
    pd.DataFrame([{
        "Report_Generated_On": date.today().isoformat(),
        "Verified_Two_Year_Universe": len(verified),
        "Rows_Assessed": len(report),
        "Fast_Fundamental_Pass_Count": len(passed),
        **source_hashes(),
        "Status": "PROVISIONAL_SCREEN_COMPLETE",
        "Scope": "PROVISIONAL_LOWER_CONFIDENCE_RESEARCH_ONLY",
        "Criteria_Note": (
            "Two verified annual years only; positive revenue/profit/EPS trend, positive margins, "
            "margin decline <=3pp, latest debt/equity <=1.0. No four-year, sector, ROE/ROCE, FCF, "
            "or point-in-time historical fundamental validation."
        ),
    }]).to_csv(SUMMARY, index=False)
    print(
        f"Fast provisional fundamental screen: {len(passed)} pass of {len(report)} assessed; "
        "strict shortlist unchanged."
    )


if __name__ == "__main__":
    main()
