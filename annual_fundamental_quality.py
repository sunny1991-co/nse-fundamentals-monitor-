#!/usr/bin/env python3
"""Strict annual fundamental quality screen for the NSE baseline CSVs.

This script deliberately distinguishes a clean two-year baseline from a complete
fundamental screen. Values that are not present in the supplied files remain
missing; unavailable metrics are listed as blockers, never treated as zero.
"""
from __future__ import annotations

from pathlib import Path
import math
import pandas as pd

ROOT = Path(__file__).resolve().parent
YEAR_FILES = {
    "2024-25": "FY2425.csv",
    "2025-26": "NSE_FY2025-26_Consolidated_Fundamentals.csv",
}
REQUIRED = [
    "Revenue_Operations_Cr", "Net_Profit_Cr", "Basic_EPS_Rs",
    "Total_Assets_Cr", "Total_Equity_Cr", "Total_Borrowings_Cr",
]
REPORT = ROOT / "annual_fundamental_quality_report.csv"
GAPS = ROOT / "annual_fundamental_data_gaps.csv"
SUMMARY = ROOT / "annual_fundamental_quality_summary.csv"


def read_year(year: str, filename: str) -> pd.DataFrame:
    path = ROOT / filename
    if not path.exists():
        raise FileNotFoundError(f"Required annual baseline file not found: {filename}")
    df = pd.read_csv(path, dtype={"Symbol": str}, keep_default_na=True)
    df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
    missing_columns = [c for c in ["Symbol", "Financial_Year", "Reporting_Type", "XBRL_URL", "Status", *REQUIRED] if c not in df.columns]
    if missing_columns:
        raise ValueError(f"{filename} is missing required columns: {missing_columns}")
    df["Symbol"] = df["Symbol"].astype("string").str.strip().str.upper()
    df["Baseline_Year"] = year
    for col in REQUIRED + [
        "Other_Income_Cr", "Total_Income_Cr", "Total_Expenses_Cr",
        "Profit_Before_Tax_Cr", "Current_Borrowings_Cr",
        "Noncurrent_Borrowings_Cr", "Cash_CashEquivalents_Cr",
    ]:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def pct_change(old, new):
    old, new = num(old), num(new)
    if old is None or new is None or old <= 0:
        return None
    return (new / old - 1.0) * 100.0


def margin(numerator, denominator):
    numerator, denominator = num(numerator), num(denominator)
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator * 100.0


def divide(numerator, denominator):
    numerator, denominator = num(numerator), num(denominator)
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def clean_url(value):
    return isinstance(value, str) and value.startswith("https://nsearchives.nseindia.com/corporate/xbrl/")


def main():
    years = {year: read_year(year, filename) for year, filename in YEAR_FILES.items()}
    duplicate_symbols = {
        year: set(df.loc[df["Symbol"].duplicated(keep=False), "Symbol"].dropna().astype(str))
        for year, df in years.items()
    }
    all_rows = []
    for year, df in years.items():
        for _, row in df.iterrows():
            symbol = str(row.get("Symbol", "") or "").strip().upper()
            reasons = []
            if not symbol or symbol == "<NA>":
                reasons.append("SYMBOL_MISSING")
            if symbol in duplicate_symbols[year]:
                reasons.append("DUPLICATE_SYMBOL_IN_YEAR")
            if str(row.get("Financial_Year", "")).strip() != year:
                reasons.append("FINANCIAL_YEAR_MISMATCH")
            if str(row.get("Reporting_Type", "")).strip().lower() != "audited consolidated":
                reasons.append("REPORTING_TYPE_NOT_AUDITED_CONSOLIDATED")
            if not clean_url(str(row.get("XBRL_URL", ""))):
                reasons.append("OFFICIAL_NSE_XBRL_SOURCE_URL_MISSING")
            if str(row.get("Status", "")).strip().upper() != "OK":
                reasons.append("SOURCE_EXTRACTION_STATUS_NOT_OK")
            for field in REQUIRED:
                if num(row.get(field)) is None:
                    reasons.append("MISSING_" + field)
            for field in ["Revenue_Operations_Cr", "Total_Assets_Cr", "Total_Borrowings_Cr"]:
                value = num(row.get(field))
                if value is not None and value < 0:
                    reasons.append("NEGATIVE_VALUE_REVIEW_" + field)
            current = num(row.get("Current_Borrowings_Cr"))
            noncurrent = num(row.get("Noncurrent_Borrowings_Cr"))
            total = num(row.get("Total_Borrowings_Cr"))
            if current is not None and noncurrent is not None and total is not None:
                if abs((current + noncurrent) - total) > max(0.05, abs(total) * 0.005):
                    reasons.append("BORROWINGS_COMPONENT_MISMATCH")
            all_rows.append({
                "Symbol": symbol,
                "Company": str(row.get("Company", "") or ""),
                "Financial_Year": year,
                "Baseline_Source_Status": str(row.get("Status", "") or ""),
                "Reporting_Type": str(row.get("Reporting_Type", "") or ""),
                "XBRL_URL": str(row.get("XBRL_URL", "") or ""),
                **{field: num(row.get(field)) for field in REQUIRED},
                "Other_Income_Cr": num(row.get("Other_Income_Cr")),
                "Total_Income_Cr": num(row.get("Total_Income_Cr")),
                "Total_Expenses_Cr": num(row.get("Total_Expenses_Cr")),
                "Profit_Before_Tax_Cr": num(row.get("Profit_Before_Tax_Cr")),
                "Current_Borrowings_Cr": current,
                "Noncurrent_Borrowings_Cr": noncurrent,
                "Cash_CashEquivalents_Cr": num(row.get("Cash_CashEquivalents_Cr")),
                "Year_Data_Quality": "PASS" if not reasons else "FAIL",
                "Year_Data_Quality_Reasons": "|".join(dict.fromkeys(reasons)),
            })

    long_df = pd.DataFrame(all_rows)
    # For pairwise comparisons, use only symbols appearing exactly once in each annual file.
    unique_sets = {
        year: set(df.loc[~df["Symbol"].isin(duplicate_symbols[year]), "Symbol"].dropna().astype(str))
        for year, df in years.items()
    }
    prior = long_df[long_df["Financial_Year"] == "2024-25"].set_index("Symbol", drop=False)
    current = long_df[long_df["Financial_Year"] == "2025-26"].set_index("Symbol", drop=False)
    symbols = sorted(set(prior.index.astype(str)) | set(current.index.astype(str)))
    rows = []
    gap_rows = []
    for symbol in symbols:
        old = prior.loc[symbol] if symbol in prior.index else None
        new = current.loc[symbol] if symbol in current.index else None
        if isinstance(old, pd.DataFrame):
            old = old.iloc[-1]
        if isinstance(new, pd.DataFrame):
            new = new.iloc[-1]
        company = str((new if new is not None else old).get("Company", "") or "")
        gaps = []
        if old is None:
            gaps.append("FY2024-25_ROW_MISSING")
        if new is None:
            gaps.append("FY2025-26_ROW_MISSING")
        if old is not None and str(old["Year_Data_Quality"]) != "PASS":
            gaps.append("FY2024-25_SOURCE_OR_CORE_DATA_CHECK_FAILED")
        if new is not None and str(new["Year_Data_Quality"]) != "PASS":
            gaps.append("FY2025-26_SOURCE_OR_CORE_DATA_CHECK_FAILED")
        if symbol not in unique_sets["2024-25"] or symbol not in unique_sets["2025-26"]:
            if old is not None and new is not None:
                gaps.append("DUPLICATE_SYMBOL_REVIEW_REQUIRED")

        def get(row, field):
            return num(row.get(field)) if row is not None else None

        old_revenue, new_revenue = get(old, "Revenue_Operations_Cr"), get(new, "Revenue_Operations_Cr")
        old_profit, new_profit = get(old, "Net_Profit_Cr"), get(new, "Net_Profit_Cr")
        old_eps, new_eps = get(old, "Basic_EPS_Rs"), get(new, "Basic_EPS_Rs")
        old_equity, new_equity = get(old, "Total_Equity_Cr"), get(new, "Total_Equity_Cr")
        old_debt, new_debt = get(old, "Total_Borrowings_Cr"), get(new, "Total_Borrowings_Cr")
        old_assets, new_assets = get(old, "Total_Assets_Cr"), get(new, "Total_Assets_Cr")
        old_cash, new_cash = get(old, "Cash_CashEquivalents_Cr"), get(new, "Cash_CashEquivalents_Cr")
        old_npm, new_npm = margin(old_profit, old_revenue), margin(new_profit, new_revenue)
        old_roe_proxy, new_roe_proxy = margin(old_profit, old_equity), margin(new_profit, new_equity)
        avg_equity = (old_equity + new_equity) / 2 if old_equity is not None and new_equity is not None else None
        new_roe_avg = margin(new_profit, avg_equity)
        old_de_ratio, new_de_ratio = divide(old_debt, old_equity), divide(new_debt, new_equity)
        old_debt_cash, new_debt_cash = (old_debt - old_cash if old_debt is not None and old_cash is not None else None), (new_debt - new_cash if new_debt is not None and new_cash is not None else None)
        baseline_pass = not gaps and old is not None and new is not None
        full_gaps = []
        if old is None or new is None:
            full_gaps.append("TWO_YEAR_ANNUAL_BASELINE_NOT_AVAILABLE")
        full_gaps.append("3_TO_5_YEAR_HISTORY_NOT_IN_BASELINE_FILES")
        full_gaps.append("ROCE_REQUIRES_VERIFIED_EBIT_AND_CAPITAL_EMPLOYED")
        full_gaps.append("INTEREST_COVERAGE_REQUIRES_VERIFIED_EBIT_AND_FINANCE_COST")
        full_gaps.append("OPERATING_CASH_FLOW_NOT_IN_BASELINE_FILES")
        full_gaps.append("FREE_CASH_FLOW_REQUIRES_OPERATING_CASH_FLOW_AND_CAPEX")
        full_gaps.append("CASH_FLOW_VS_PROFIT_NOT_AVAILABLE")
        full_gaps.append("SECTOR_CLASSIFICATION_REQUIRED_FOR_SECTOR_SPECIFIC_CHECKS")
        full_gaps.append("QUARTERLY_CONSOLIDATED_BASIS_NOT_YET_VERIFIED")
        rows.append({
            "Symbol": symbol,
            "Company": company,
            "Has_FY2024_25": old is not None,
            "Has_FY2025_26": new is not None,
            "FY2024_25_Source_Data_Quality": str(old["Year_Data_Quality"]) if old is not None else "MISSING",
            "FY2025_26_Source_Data_Quality": str(new["Year_Data_Quality"]) if new is not None else "MISSING",
            "Two_Year_Baseline_Data_Quality": "PASS" if baseline_pass else "FAIL",
            "Baseline_Failure_Reasons": "|".join(dict.fromkeys(gaps)),
            "FY2024_25_Revenue_Cr": old_revenue,
            "FY2025_26_Revenue_Cr": new_revenue,
            "Revenue_Growth_1Y_Pct": pct_change(old_revenue, new_revenue),
            "Revenue_Growth_Interpretation": "Growth" if new_revenue is not None and old_revenue is not None and new_revenue > old_revenue else ("Decline_or_unchanged" if new_revenue is not None and old_revenue is not None else "Unavailable"),
            "FY2024_25_Net_Profit_Cr": old_profit,
            "FY2025_26_Net_Profit_Cr": new_profit,
            "Net_Profit_Growth_1Y_Pct": pct_change(old_profit, new_profit),
            "Net_Profit_Growth_Interpretation": "Growth" if new_profit is not None and old_profit is not None and new_profit > old_profit else ("Decline_or_unchanged" if new_profit is not None and old_profit is not None else "Unavailable"),
            "FY2024_25_EPS_Rs": old_eps,
            "FY2025_26_EPS_Rs": new_eps,
            "EPS_Growth_1Y_Pct": pct_change(old_eps, new_eps),
            "EPS_Growth_Interpretation": "Growth" if new_eps is not None and old_eps is not None and new_eps > old_eps else ("Decline_or_unchanged" if new_eps is not None and old_eps is not None else "Unavailable"),
            "FY2024_25_Net_Profit_Margin_Pct": old_npm,
            "FY2025_26_Net_Profit_Margin_Pct": new_npm,
            "FY2024_25_ROE_YearEnd_Proxy_Pct": old_roe_proxy,
            "FY2025_26_ROE_YearEnd_Proxy_Pct": new_roe_proxy,
            "FY2025_26_ROE_Average_Equity_Pct": new_roe_avg,
            "FY2024_25_Debt_Equity": old_de_ratio,
            "FY2025_26_Debt_Equity": new_de_ratio,
            "FY2024_25_Net_Debt_Cr": old_debt_cash,
            "FY2025_26_Net_Debt_Cr": new_debt_cash,
            "Three_To_Five_Year_Growth_Status": "MISSING_REQUIRED_HISTORY",
            "ROCE_Status": "NOT_CALCULABLE_FROM_BASELINE_FIELDS",
            "Interest_Coverage_Status": "NOT_CALCULABLE_FROM_BASELINE_FIELDS",
            "Operating_Cash_Flow_Status": "NOT_AVAILABLE_IN_BASELINE",
            "Free_Cash_Flow_Status": "NOT_AVAILABLE_IN_BASELINE",
            "Cash_Flow_vs_Profit_Status": "NOT_AVAILABLE_IN_BASELINE",
            "Quarterly_Reverification_Status": "BLOCKED_UNTIL_CONSOLIDATED_BASIS_VERIFIED",
            "Sector_Specific_Status": "REQUIRES_SECTOR_CLASSIFICATION",
            "Full_Fundamental_Criteria_Pass": False,
            "Full_Screen_Blockers": "|".join(dict.fromkeys(full_gaps)),
        })
        if gaps or full_gaps:
            gap_rows.append({
                "Symbol": symbol, "Company": company,
                "Baseline_Failure_Reasons": "|".join(dict.fromkeys(gaps)),
                "Full_Screen_Blockers": "|".join(dict.fromkeys(full_gaps)),
                "Full_Fundamental_Criteria_Pass": False,
            })

    report = pd.DataFrame(rows)
    if not report.empty:
        report = report.sort_values(["Two_Year_Baseline_Data_Quality", "Symbol"])
    report.to_csv(REPORT, index=False, na_rep="")
    pd.DataFrame(gap_rows).to_csv(GAPS, index=False, na_rep="")

    summaries = []
    for year, df in years.items():
        source_rows = long_df[long_df["Financial_Year"] == year]
        summaries.append({
            "Financial_Year": year,
            "Rows": len(df),
            "Unique_Symbols": int(df["Symbol"].nunique(dropna=True)),
            "Duplicate_Symbol_Rows": int(df["Symbol"].duplicated(keep=False).sum()),
            "Source_Status_OK": int(df["Status"].astype(str).str.upper().eq("OK").sum()),
            "Audited_Consolidated_Rows": int(df["Reporting_Type"].astype(str).str.lower().eq("audited consolidated").sum()),
            "Official_NSE_XBRL_URL_Rows": int(df["XBRL_URL"].astype(str).str.startswith("https://nsearchives.nseindia.com/corporate/xbrl/").sum()),
            "Six_Core_Fields_Complete": int(df[REQUIRED].notna().all(axis=1).sum()),
            "Year_Data_Quality_Pass": int(source_rows["Year_Data_Quality"].eq("PASS").sum()),
            "Rows_Requiring_Data_Review": int(source_rows["Year_Data_Quality"].ne("PASS").sum()),
        })
    summaries.append({
        "Financial_Year": "PAIRWISE_SUMMARY",
        "Rows": len(report),
        "Unique_Symbols": int(report["Symbol"].nunique()) if not report.empty else 0,
        "Duplicate_Symbol_Rows": 0,
        "Source_Status_OK": 0,
        "Audited_Consolidated_Rows": 0,
        "Official_NSE_XBRL_URL_Rows": 0,
        "Six_Core_Fields_Complete": 0,
        "Year_Data_Quality_Pass": int(report["Two_Year_Baseline_Data_Quality"].eq("PASS").sum()) if not report.empty else 0,
        "Rows_Requiring_Data_Review": int(report["Two_Year_Baseline_Data_Quality"].ne("PASS").sum()) if not report.empty else 0,
    })
    pd.DataFrame(summaries).to_csv(SUMMARY, index=False, na_rep="")
    print(f"Saved {REPORT.name}, {GAPS.name}, {SUMMARY.name}")
    print(f"Symbols assessed: {len(report)}")
    print(f"Two-year baseline quality pass: {int(report['Two_Year_Baseline_Data_Quality'].eq('PASS').sum()) if not report.empty else 0}")
    print("Full fundamental screen pass: 0 (required 3-5 year, cash-flow, ROCE/coverage, sector, and quarterly verification inputs are not all available yet).")


if __name__ == "__main__":
    main()
