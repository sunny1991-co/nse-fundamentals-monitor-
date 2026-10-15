#!/usr/bin/env python3
"""Strict 4-year growth / profitability / leverage / cash-flow screen."""
from __future__ import annotations

from pathlib import Path
from datetime import date
import math
import pandas as pd

ROOT = Path(__file__).resolve().parent
BASELINE_FILES = {"2024-25": "FY2425.csv", "2025-26": "NSE_FY2025-26_Consolidated_Fundamentals.csv"}
HISTORY_FILE = ROOT / "annual_historical_results.csv"
SECTOR_FILE = ROOT / "nse_sector_map.csv"
REPORT = ROOT / "annual_fundamental_quality_report.csv"
GAPS = ROOT / "annual_fundamental_data_gaps.csv"
SUMMARY = ROOT / "annual_fundamental_quality_summary.csv"
CORE = ["Revenue_Operations_Cr", "Net_Profit_Cr", "Basic_EPS_Rs", "Total_Assets_Cr", "Total_Equity_Cr", "Total_Borrowings_Cr"]
EXTRA = [
    "Profit_Before_Tax_Cr", "Total_Expenses_Cr", "Finance_Cost_Cr",
    "Depreciation_Amortisation_Cr", "Cash_Flow_Operating_Cr", "CapEx_Cr",
    "Current_Liabilities_Cr", "Cash_CashEquivalents_Cr",
]
FOUR_YEARS = ["2022-23", "2023-24", "2024-25", "2025-26"]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
    return df


def number(x):
    try:
        n = float(str(x).replace(",", "").strip())
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def blank(x):
    return x is None or str(x).strip().lower() in {"", "nan", "none", "nat", "<na>"}


def url_ok(x):
    return isinstance(x, str) and x.startswith("https://nsearchives.nseindia.com/corporate/xbrl/")


def bool_value(x):
    return str(x).strip().lower() in {"true", "1", "yes", "ok", "verified"}


def financial_year_from_end(end):
    try:
        y = int(str(end)[:4])
        return f"{y-1}-{str(y)[-2:]}"
    except (ValueError, TypeError):
        return ""


def read_baselines():
    result = []
    for year, file in BASELINE_FILES.items():
        path = ROOT / file
        if not path.exists():
            raise FileNotFoundError(f"Missing annual baseline: {file}")
        df = read_csv(path)
        required = ["Symbol", "Financial_Year", "Reporting_Type", "XBRL_URL", "Status", *CORE]
        missing = [x for x in required if x not in df]
        if missing:
            raise ValueError(f"{file} lacks columns: {missing}")
        for _, raw in df.iterrows():
            sym = str(raw.get("Symbol", "")).strip().upper()
            if not sym:
                continue
            typ = str(raw.get("Reporting_Type", "")).strip()
            source = str(raw.get("XBRL_URL", "")).strip()
            status = str(raw.get("Status", "")).strip().upper()
            # These are baseline claims only. They become independently verified
            # only when the same XBRL URL is confirmed by NSE filing metadata + XML.
            row = {
                "Symbol": sym,
                "Company": str(raw.get("Company", "") or ""),
                "Financial_Year": year,
                "Fiscal_Year_End": f"{int(year[:4])+1}-03-31",
                "Reporting_Basis": "Consolidated" if "consolidated" in typ.lower() else "",
                "Audit_Status": "Audited" if "audited" in typ.lower() else "",
                "NSE_Consolidation_Status": "Consolidated" if "consolidated" in typ.lower() else "",
                "NSE_Consolidation_Verified": False,
                "Source_Verification_Status": "PENDING_NSE_METADATA_XBRL_CROSSCHECK",
                "Baseline_Source_Status": status,
                "Source_URL": source,
                "Filing_Published_At": "",
                "Revision_Date": "",
                "Submission_Subtype": "",
                "Revision_Remark": "",
            }
            for name in CORE + EXTRA:
                raw_value = raw.get(name, "")
                if name == "Profit_Before_Tax_Cr" and blank(raw_value):
                    raw_value = raw.get("Profit_Before_Tax_Cr", "")
                row[name] = number(raw_value)
            row["Missing_Core_Fields"] = "|".join(x for x in CORE if row.get(x) is None)
            row["Six_Core_Fields_Complete"] = not row["Missing_Core_Fields"]
            result.append(row)
    return result


def standardize_history():
    records = read_baselines()
    history = read_csv(HISTORY_FILE)
    if not history.empty:
        for _, raw in history.iterrows():
            sym = str(raw.get("Symbol", "")).strip().upper()
            fy = str(raw.get("Financial_Year", "")).strip() or financial_year_from_end(raw.get("Fiscal_Year_End"))
            if not sym or not fy:
                continue
            row = {"Symbol": sym, "Financial_Year": fy}
            fields = [
                "Company", "Fiscal_Year_End", "Reporting_Basis", "Audit_Status",
                "NSE_Consolidation_Status", "NSE_Consolidation_Verified",
                "Source_URL", "Filing_Published_At", "Revision_Date",
                "Submission_Subtype", "Revision_Remark", "Missing_Core_Fields",
                "Six_Core_Fields_Complete", "Status", "CapEx_Components_Verified", "CapEx_Method",
            ] + CORE + EXTRA
            for field in fields:
                row[field] = raw.get(field, "")
            row["Source_Verification_Status"] = "NSE_INDEX_AND_XBRL_BASIS_MATCH" if bool_value(raw.get("NSE_Consolidation_Verified")) else "UNVERIFIED"
            row["Baseline_Source_Status"] = str(raw.get("Status", ""))
            for field in CORE + EXTRA:
                row[field] = number(raw.get(field, ""))
            records.append(row)

    # Collapse revisions by symbol/year while merging historical cash-flow metrics
    # from the annual XBRL extraction and core metrics from the baseline.
    merged = {}
    for row in records:
        sym = str(row.get("Symbol", "")).strip().upper()
        fy = str(row.get("Financial_Year", "")).strip()
        if not sym or not fy:
            continue
        key = (sym, fy)
        if key not in merged:
            merged[key] = row.copy()
            continue
        current, incoming = merged[key], row
        is_history = bool_value(incoming.get("NSE_Consolidation_Verified"))
        current_is_history = bool_value(current.get("NSE_Consolidation_Verified"))
        if is_history:
            # Once a source URL is independently verified, its own facts are
            # authoritative. Do not fill a missing verified fact with a value
            # extracted from another revision/source; preserve it as missing.
            incoming_stamp = str(incoming.get("Filing_Published_At", "") or "")
            current_stamp = str(current.get("Filing_Published_At", "") or "")
            replace_verified = (not current_is_history) or (incoming_stamp and incoming_stamp >= current_stamp)
            if replace_verified:
                for field in [
                    "Reporting_Basis", "Audit_Status", "NSE_Consolidation_Status",
                    "NSE_Consolidation_Verified", "Source_Verification_Status",
                    "Source_URL", "Filing_Published_At", "Revision_Date",
                    "Submission_Subtype", "Revision_Remark", "Baseline_Source_Status", "Status",
                    "CapEx_Components_Verified", "CapEx_Method",
                ]:
                    current[field] = incoming.get(field, "")
                for field in CORE + EXTRA:
                    current[field] = number(incoming.get(field))
        elif not current_is_history:
            # Baseline-only rows are not independently source-verified and will
            # fail the final gate. Retain numeric observations for gap reporting,
            # without treating them as verified evidence.
            for field in CORE + EXTRA:
                if number(current.get(field)) is None and number(incoming.get(field)) is not None:
                    current[field] = number(incoming.get(field))
        if blank(current.get("Company")) and not blank(incoming.get("Company")):
            current["Company"] = incoming.get("Company")
        merged[key] = current

    out = []
    for (sym, fy), row in merged.items():
        row["Symbol"], row["Financial_Year"] = sym, fy
        if blank(row.get("Fiscal_Year_End")):
            try:
                end_year = int(fy.split("-")[0]) + 1
                row["Fiscal_Year_End"] = f"{end_year}-03-31"
            except Exception:
                row["Fiscal_Year_End"] = ""
        typ_ok = str(row.get("Reporting_Basis", "")).lower() == "consolidated" and "audit" in str(row.get("Audit_Status", "")).lower() and "un-audit" not in str(row.get("Audit_Status", "")).lower() and "unaudit" not in str(row.get("Audit_Status", "")).lower()
        url = str(row.get("Source_URL", "") or "")
        status_ok = bool_value(row.get("NSE_Consolidation_Verified")) and str(row.get("Source_Verification_Status", "")) == "NSE_INDEX_AND_XBRL_BASIS_MATCH"
        missing = [field for field in CORE if number(row.get(field)) is None]
        reasons = []
        if not sym:
            reasons.append("SYMBOL_MISSING")
        if fy not in {f"{y}-{str(y+1)[-2:]}" for y in range(2020, 2027)}:
            reasons.append("FINANCIAL_YEAR_UNRECOGNIZED")
        if not typ_ok:
            reasons.append("NOT_VERIFIED_AUDITED_CONSOLIDATED")
        if not url_ok(url):
            reasons.append("OFFICIAL_NSE_XBRL_URL_MISSING")
        if not status_ok:
            reasons.append("NSE_METADATA_AND_XBRL_BASIS_NOT_CROSS_VERIFIED_FOR_THIS_SOURCE")
        if missing:
            reasons.append("MISSING_CORE_FIELDS:" + "|".join(missing))
        row["Missing_Core_Fields"] = "|".join(missing)
        row["Year_Data_Quality"] = "PASS" if not reasons else "FAIL"
        row["Year_Data_Quality_Reasons"] = "|".join(reasons)
        for field in CORE + EXTRA:
            row[field] = number(row.get(field))
        out.append(row)
    return pd.DataFrame(out)


def pct_cagr(first, last, n_years=3):
    first, last = number(first), number(last)
    if first is None or last is None or first <= 0 or last < 0 or n_years <= 0:
        return None
    return ((last / first) ** (1.0 / n_years) - 1.0) * 100.0


def margin(numerator, denominator):
    a, b = number(numerator), number(denominator)
    return a / b * 100.0 if a is not None and b is not None and b > 0 else None


def ratio(numerator, denominator):
    a, b = number(numerator), number(denominator)
    return a / b if a is not None and b is not None and b > 0 else None


def truthy_series(values, predicate):
    return all(v is not None and predicate(v) for v in values)


def load_sector_map():
    df = read_csv(SECTOR_FILE)
    if df.empty or "Symbol" not in df:
        return {}
    out = {}
    for _, row in df.iterrows():
        try:
            age = (date.today() - date.fromisoformat(str(row.get("Lookup_Date", ""))[:10])).days
        except ValueError:
            continue
        if str(row.get("Lookup_Status", "")) == "OK" and 0 <= age < 60:
            out[str(row.get("Symbol", "")).upper()] = row.to_dict()
    return out


def sector_mode(sector_row):
    if not sector_row:
        return "SECTOR_CLASSIFICATION_MISSING"
    text = " ".join(str(sector_row.get(k, "")) for k in ("Macro_Economic_Sector", "Sector", "Industry", "Basic_Industry")).lower()
    specialist = ("financial services", "bank", "insurance", "nbfc", "non banking", "mutual fund", "asset management", "reit", "invit", "investment trust", "real estate", "power generation", "utilities", "infrastructure")
    if any(x in text for x in specialist):
        return "SPECIALIST_SECTOR_METRICS_REQUIRED"
    return "GENERAL_NONFINANCIAL_SCREEN"


def main():
    annual = standardize_history()
    if annual.empty:
        raise RuntimeError("No annual rows found. Run nse_annual_history.py first.")
    sector_map = load_sector_map()
    annual["Financial_Year"] = annual["Financial_Year"].astype(str)
    annual["Symbol"] = annual["Symbol"].astype(str).str.upper()
    annual["FY_Start_Year"] = annual["Financial_Year"].str.slice(0, 4)
    annual["FY_Start_Year"] = pd.to_numeric(annual["FY_Start_Year"], errors="coerce")
    annual = annual.sort_values(["Symbol", "FY_Start_Year"])
    duplicates = annual.duplicated(["Symbol", "Financial_Year"], keep=False)
    if duplicates.any():
        annual.loc[duplicates, "Year_Data_Quality"] = "FAIL"
        annual.loc[duplicates, "Year_Data_Quality_Reasons"] = annual.loc[duplicates, "Year_Data_Quality_Reasons"].astype(str) + "|DUPLICATE_SYMBOL_YEAR_REVIEW"

    rows = []
    for sym, group in annual.groupby("Symbol"):
        group = group.sort_values("FY_Start_Year").drop_duplicates("Financial_Year", keep="last")
        by_year = {str(r["Financial_Year"]): r for _, r in group.iterrows()}
        latest = by_year.get("2025-26")
        prior = by_year.get("2024-25")
        company = str((latest if latest is not None else (prior if prior is not None else group.iloc[-1])).get("Company", "") or "")
        blockers = []
        if latest is None or prior is None:
            blockers.append("BOTH_FY2024_25_AND_FY2025_26_REQUIRED")
        elif latest.get("Year_Data_Quality") != "PASS" or prior.get("Year_Data_Quality") != "PASS":
            blockers.append("TWO_YEAR_ANNUAL_VERIFICATION_FAILED_OR_INCOMPLETE")

        recent = [by_year.get(y) for y in FOUR_YEARS]
        has_four = all(r is not None and r.get("Year_Data_Quality") == "PASS" for r in recent)
        if not has_four:
            blockers.append("FOUR_CONSECUTIVE_VERIFIED_ANNUAL_YEARS_REQUIRED_FOR_3Y_GROWTH")
        get = lambda r, field: number(r.get(field)) if r is not None else None

        revenue_cagr = profit_cagr = eps_cagr = None
        if all(r is not None for r in recent):
            revenue_cagr = pct_cagr(get(recent[0], "Revenue_Operations_Cr"), get(recent[-1], "Revenue_Operations_Cr"))
            profit_values = [get(r, "Net_Profit_Cr") for r in recent]
            eps_values = [get(r, "Basic_EPS_Rs") for r in recent]
            if truthy_series(profit_values, lambda x: x > 0):
                profit_cagr = pct_cagr(profit_values[0], profit_values[-1])
            if truthy_series(eps_values, lambda x: x > 0):
                eps_cagr = pct_cagr(eps_values[0], eps_values[-1])
        growth_pass = (
            revenue_cagr is not None and revenue_cagr >= 10
            and profit_cagr is not None and profit_cagr >= 10
            and eps_cagr is not None and eps_cagr >= 10
            and truthy_series([get(r, "Revenue_Operations_Cr") for r in recent], lambda x: x > 0)
            and truthy_series([get(r, "Net_Profit_Cr") for r in recent], lambda x: x > 0)
            and truthy_series([get(r, "Basic_EPS_Rs") for r in recent], lambda x: x > 0)
        )
        if not growth_pass:
            blockers.append("SALES_PROFIT_EPS_3Y_CAGR_MUST_EACH_BE_AT_LEAST_10_PERCENT_AND_POSITIVE")

        npm_values = [margin(get(r, "Net_Profit_Cr"), get(r, "Revenue_Operations_Cr")) if r is not None else None for r in recent]
        npm_valid = truthy_series(npm_values, lambda x: x > 0)
        declines = []
        if all(v is not None for v in npm_values):
            declines = [npm_values[i] < npm_values[i-1] for i in range(1, len(npm_values))]
        two_consecutive_margin_declines = any(declines[i] and declines[i+1] for i in range(max(0, len(declines)-1)))
        margin_pass = npm_valid and not two_consecutive_margin_declines and npm_values[-1] >= npm_values[-2] - 2.0
        if not margin_pass:
            blockers.append("POSITIVE_MARGIN_AND_NO_TWO_CONSECUTIVE_MATERIAL_MARGIN_DECLINES_REQUIRED")

        # Calculate ROE and ROCE for each year using average year-end balances.
        roe_values, roce_values, interest_cover_values, debt_equity_values = {}, {}, {}, {}
        sorted_years = sorted(by_year)
        for idx, y in enumerate(sorted_years):
            current = by_year[y]
            if idx == 0:
                continue
            previous = by_year[sorted_years[idx-1]]
            curr_equity, prev_equity = get(current, "Total_Equity_Cr"), get(previous, "Total_Equity_Cr")
            average_equity = (curr_equity + prev_equity)/2 if curr_equity is not None and prev_equity is not None else None
            roe_values[y] = margin(get(current, "Net_Profit_Cr"), average_equity)
            debt_equity_values[y] = ratio(get(current, "Total_Borrowings_Cr"), curr_equity)
            pbt, finance = get(current, "Profit_Before_Tax_Cr"), get(current, "Finance_Cost_Cr")
            ebit = pbt + finance if pbt is not None and finance is not None else None
            curr_ce = (get(current, "Total_Assets_Cr") - get(current, "Current_Liabilities_Cr")) if get(current, "Total_Assets_Cr") is not None and get(current, "Current_Liabilities_Cr") is not None else None
            prev_ce = (get(previous, "Total_Assets_Cr") - get(previous, "Current_Liabilities_Cr")) if get(previous, "Total_Assets_Cr") is not None and get(previous, "Current_Liabilities_Cr") is not None else None
            average_ce = (curr_ce + prev_ce)/2 if curr_ce is not None and prev_ce is not None else None
            roce_values[y] = margin(ebit, average_ce)
            if finance is not None and finance > 0 and ebit is not None:
                interest_cover_values[y] = ebit / finance
            elif finance == 0 and get(current, "Total_Borrowings_Cr") == 0:
                interest_cover_values[y] = None
            else:
                interest_cover_values[y] = None

        recent_metric_years = ["2023-24", "2024-25", "2025-26"]
        roe_recent = [roe_values.get(y) for y in recent_metric_years]
        roce_recent = [roce_values.get(y) for y in recent_metric_years]
        ic_latest = interest_cover_values.get("2025-26")
        de_latest = debt_equity_values.get("2025-26")
        roe_pass = truthy_series(roe_recent, lambda x: x >= 15) and roe_values.get("2025-26", 0) >= 15
        roce_pass = truthy_series(roce_recent, lambda x: x >= 15)
        debt_pass = de_latest is not None and de_latest <= 0.5 and get(latest, "Total_Equity_Cr") is not None and get(latest, "Total_Equity_Cr") > 0
        latest_finance_cost = get(latest, "Finance_Cost_Cr")
        latest_total_debt = get(latest, "Total_Borrowings_Cr")
        # If both debt and finance cost are explicitly zero, interest coverage
        # is not applicable rather than a failure.
        interest_not_applicable = latest_finance_cost == 0 and latest_total_debt == 0
        interest_pass = (ic_latest is not None and ic_latest >= 3) or interest_not_applicable
        if not roe_pass:
            blockers.append("ROE_MUST_BE_AT_LEAST_15_PERCENT_IN_LAST_3_YEARS")
        if not roce_pass:
            blockers.append("ROCE_MUST_BE_AT_LEAST_15_PERCENT_IN_LAST_3_YEARS")
        if not debt_pass:
            blockers.append("DEBT_TO_EQUITY_MUST_BE_AT_MOST_0_5_WITH_POSITIVE_EQUITY")
        if not interest_pass:
            blockers.append("INTEREST_COVERAGE_MUST_BE_AT_LEAST_3X")

        cash_years = ["2023-24", "2024-25", "2025-26"]
        ocf_vals = [get(by_year.get(y), "Cash_Flow_Operating_Cr") if by_year.get(y) is not None else None for y in cash_years]
        np_vals = [get(by_year.get(y), "Net_Profit_Cr") if by_year.get(y) is not None else None for y in cash_years]
        cash_flow_pass = all(v is not None and v > 0 for v in ocf_vals) and all(v is not None for v in np_vals) and sum(ocf_vals) >= 0.8 * sum(np_vals)
        fcf_vals = []
        for y in ["2022-23", "2023-24", "2024-25", "2025-26"]:
            r = by_year.get(y)
            if r is None:
                fcf_vals.append(None)
            else:
                ocf, capex = get(r, "Cash_Flow_Operating_Cr"), get(r, "CapEx_Cr")
                capex_verified = bool_value(r.get("CapEx_Components_Verified"))
                fcf_vals.append(ocf - capex if ocf is not None and capex is not None and capex_verified else None)
        fcf_pass = sum(1 for v in fcf_vals if v is not None and v > 0) >= 3 and sum(1 for v in fcf_vals if v is None) == 0
        if not cash_flow_pass:
            blockers.append("OPERATING_CASH_FLOW_MUST_BE_POSITIVE_AND_AT_LEAST_80_PERCENT_OF_3Y_NET_PROFIT")
        if not fcf_pass:
            blockers.append("FREE_CASH_FLOW_MUST_BE_POSITIVE_IN_AT_LEAST_3_OF_4_YEARS_WITH_NO_MISSING_DATA")

        sector = sector_map.get(sym)
        mode = sector_mode(sector)
        if mode == "SECTOR_CLASSIFICATION_MISSING":
            blockers.append("OFFICIAL_NSE_SECTOR_CLASSIFICATION_MISSING")
        elif mode == "SPECIALIST_SECTOR_METRICS_REQUIRED":
            blockers.append("SPECIALIST_SECTOR_METRICS_NOT_IMPLEMENTED_OR_NOT_AVAILABLE")

        rows.append({
            "Symbol": sym, "Company": company,
            "FY2024_25_Year_Data_Quality": str(prior.get("Year_Data_Quality")) if prior is not None else "MISSING",
            "FY2025_26_Year_Data_Quality": str(latest.get("Year_Data_Quality")) if latest is not None else "MISSING",
            "Two_Year_Baseline_Data_Quality": "PASS" if latest is not None and prior is not None and latest.get("Year_Data_Quality") == prior.get("Year_Data_Quality") == "PASS" else "FAIL",
            "Verified_Annual_Years": "|".join(y for y, r in by_year.items() if r.get("Year_Data_Quality") == "PASS"),
            "History_Years_Available": len(by_year),
            "Revenue_3Y_CAGR_Pct": revenue_cagr,
            "Net_Profit_3Y_CAGR_Pct": profit_cagr,
            "EPS_3Y_CAGR_Pct": eps_cagr,
            "Latest_Net_Profit_Margin_Pct": npm_values[-1] if npm_values else None,
            "Latest_ROE_Pct": roe_values.get("2025-26"),
            "Latest_ROCE_Pct": roce_values.get("2025-26"),
            "Latest_Debt_Equity": de_latest,
            "Latest_Interest_Coverage": ic_latest,
            "Three_Year_Operating_Cash_Flow_Cr": sum(ocf_vals) if all(v is not None for v in ocf_vals) else None,
            "Three_Year_Net_Profit_Cr": sum(np_vals) if all(v is not None for v in np_vals) else None,
            "Three_Year_OCF_Net_Profit_Ratio": ratio(sum(ocf_vals), sum(np_vals)) if all(v is not None for v in ocf_vals) and sum(np_vals) > 0 else None,
            "FCF_2022_23_Cr": fcf_vals[0],
            "FCF_2023_24_Cr": fcf_vals[1],
            "FCF_2024_25_Cr": fcf_vals[2],
            "FCF_2025_26_Cr": fcf_vals[3],
            "Growth_Check": "PASS" if growth_pass else "FAIL",
            "Profitability_Check": "PASS" if roe_pass and roce_pass and margin_pass else "FAIL",
            "Debt_Check": "PASS" if debt_pass and interest_pass else "FAIL",
            "Cash_Flow_Check": "PASS" if cash_flow_pass and fcf_pass else "FAIL",
            "Macro_Economic_Sector": str(sector.get("Macro_Economic_Sector", "")) if sector else "",
            "Sector": str(sector.get("Sector", "")) if sector else "",
            "Industry": str(sector.get("Industry", "")) if sector else "",
            "Basic_Industry": str(sector.get("Basic_Industry", "")) if sector else "",
            "Sector_Specific_Status": mode,
            "Full_Fundamental_Criteria_Pass": not blockers,
            "Full_Screen_Blockers": "|".join(dict.fromkeys(blockers)),
        })

    report = pd.DataFrame(rows).sort_values(["Full_Fundamental_Criteria_Pass", "Symbol"], ascending=[False, True])
    report.to_csv(REPORT, index=False, na_rep="")
    failed = report[~report["Full_Fundamental_Criteria_Pass"].astype(bool)].copy()
    failed.to_csv(GAPS, index=False, na_rep="")
    summary_rows = []
    for year, df in annual.groupby("Financial_Year"):
        source_verified = df.get("Source_Verification_Status", pd.Series([""] * len(df), index=df.index)).astype(str).eq("NSE_INDEX_AND_XBRL_BASIS_MATCH")
        summary_rows.append({
            "Financial_Year": str(year),
            "Rows_Assessed": len(df),
            "Unique_Symbols": int(df["Symbol"].nunique()),
            "Six_Core_Fields_Complete": int(df[CORE].notna().all(axis=1).sum()),
            "NSE_Metadata_XBRL_Basis_Match": int(source_verified.sum()),
            "Year_Data_Quality_Pass": int(df["Year_Data_Quality"].eq("PASS").sum()),
            "Rows_Requiring_Data_Review": int(df["Year_Data_Quality"].ne("PASS").sum()),
        })
    summary_rows.append({
        "Financial_Year": "FUNDAMENTAL_SCREEN",
        "Rows_Assessed": len(report),
        "Unique_Symbols": int(report["Symbol"].nunique()) if not report.empty else 0,
        "Six_Core_Fields_Complete": int(report["Two_Year_Baseline_Data_Quality"].eq("PASS").sum()) if not report.empty else 0,
        "NSE_Metadata_XBRL_Basis_Match": 0,
        "Year_Data_Quality_Pass": int(report["Full_Fundamental_Criteria_Pass"].astype(bool).sum()) if not report.empty else 0,
        "Rows_Requiring_Data_Review": int(report["Full_Fundamental_Criteria_Pass"].astype(bool).eq(False).sum()) if not report.empty else 0,
    })
    pd.DataFrame(summary_rows).to_csv(SUMMARY, index=False, na_rep="")
    print(f"Annual rows assessed: {len(annual)}; sectors cached: {len(sector_map)}")
    print(f"Two-year baseline pass: {int((report['Two_Year_Baseline_Data_Quality'] == 'PASS').sum())}")
    print(f"Full fundamental criteria pass: {int(report['Full_Fundamental_Criteria_Pass'].astype(bool).sum())}")
    print("Saved annual fundamental quality report, gap report, and summary.")


if __name__ == "__main__":
    main()

