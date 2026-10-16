#!/usr/bin/env python3
"""Create the final shortlist only after strict annual and quarterly checks."""
from __future__ import annotations

from research_monitor.paths import data_root
from datetime import date
from pathlib import Path
import math
import pandas as pd
from research_monitor.filing_dates import QUARTERLY_PARSER_VERSION

ROOT = data_root()
ANNUAL_FILE = ROOT / "annual_fundamental_quality_report.csv"
QUARTERLY_FILE = ROOT / "quarterly_change_report.csv"
QUARTERLY_SOURCE_STATUS_FILE = ROOT / "quarterly_collection_status.csv"
SHORTLIST_FILE = ROOT / "final_fundamental_shortlist.csv"
EXCLUSIONS_FILE = ROOT / "final_fundamental_exclusions.csv"
SUMMARY_FILE = ROOT / "final_fundamental_screen_summary.csv"
FRESHNESS_DAYS = 180
GROWTH_FIELDS = {
    "Revenue": ("Revenue_Operations_Cr", "Revenue_QoQ_Change_Pct", "Revenue_YoY_Change_Pct"),
    "Net_Profit": ("Net_Profit_Cr", "Net_Profit_QoQ_Change_Pct", "Net_Profit_YoY_Change_Pct"),
    "EPS": ("Basic_EPS_Rs", "EPS_QoQ_Change_Pct", "EPS_YoY_Change_Pct"),
}
CORE_FIELDS = [
    "Revenue_Operations_Cr", "Net_Profit_Cr", "Basic_EPS_Rs",
    "Total_Assets_Cr", "Total_Equity_Cr", "Total_Borrowings_Cr",
]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
    return df


def num(value):
    try:
        result = float(str(value).replace(",", "").strip())
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def truthy(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def parse_day(value):
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (ValueError, TypeError):
        return None


def quarterly_source_readiness(path: Path) -> tuple[bool, str]:
    """Current NSE source access must be validated independently of old facts.

    Historical financial records remain stored after a 403, but must not
    authorize a new tradable shortlist using stale collector state.
    """
    frame = read_csv(path)
    if frame.empty or not {"Run_Date", "Collection_Status"}.issubset(frame.columns):
        return False, "QUARTERLY_SOURCE_STATUS_MISSING"
    last = frame.iloc[-1]
    if str(last.get("Collection_Status", "")) != "OK":
        return False, "QUARTERLY_SOURCE_ACCESS_NOT_VERIFIED"
    stamp = parse_day(last.get("Run_Date", ""))
    if stamp is None or not 0 <= (date.today() - stamp).days <= 1:
        return False, "QUARTERLY_SOURCE_STATUS_STALE"
    if str(last.get("Parser_Version", "")) != QUARTERLY_PARSER_VERSION:
        return False, "QUARTERLY_PARSER_REVERIFICATION_REQUIRED"
    return True, "OK"


def latest_three_quarters(rows: pd.DataFrame) -> list[pd.Series]:
    if rows.empty:
        return []
    frame = rows.copy()
    frame["_date"] = pd.to_datetime(frame["Quarter_End"], errors="coerce")
    frame["_published"] = pd.to_datetime(frame.get("Filing_Published_At", ""), errors="coerce")
    frame = frame.sort_values(["_date", "_published"]).drop_duplicates(["Symbol", "Quarter_End"], keep="last")
    return [r for _, r in frame.tail(3).iterrows()]


def main():
    annual = read_csv(ANNUAL_FILE)
    quarterly = read_csv(QUARTERLY_FILE)
    if "Parser_Version" in quarterly.columns:
        quarterly = quarterly[quarterly["Parser_Version"].astype(str).eq(QUARTERLY_PARSER_VERSION)].copy()
    else:
        quarterly = quarterly.iloc[0:0].copy()
    source_ready, source_reason = quarterly_source_readiness(QUARTERLY_SOURCE_STATUS_FILE)
    if not source_ready:
        print(f"Quarterly collector not ready: {source_reason}; final trade eligibility blocked.")
    if annual.empty or "Symbol" not in annual or "Full_Fundamental_Criteria_Pass" not in annual:
        raise RuntimeError("Annual quality report missing/empty; final shortlist cannot be produced.")
    if not quarterly.empty and "Symbol" in quarterly:
        quarterly["Symbol"] = quarterly["Symbol"].astype(str).str.strip().str.upper()
    annual["Symbol"] = annual["Symbol"].astype(str).str.strip().str.upper()
    q_by_symbol = {s: g.copy() for s, g in quarterly.groupby("Symbol")} if not quarterly.empty and "Symbol" in quarterly else {}
    short_rows, excluded_rows = [], []

    for _, a in annual.iterrows():
        sym = str(a.get("Symbol", "")).strip().upper()
        if not sym:
            continue
        company = str(a.get("Company", "") or "")
        annual_pass = truthy(a.get("Full_Fundamental_Criteria_Pass"))
        annual_blockers = str(a.get("Full_Screen_Blockers", "") or "")
        blockers = []
        latest_row = None
        qrows = q_by_symbol.get(sym, pd.DataFrame())
        if not annual_pass:
            blockers.append("ANNUAL_FUNDAMENTAL_SCREEN_FAILED")
            if annual_blockers:
                blockers.append(annual_blockers)
            quarterly_status = "NOT_CHECKED_ANNUAL_SCREEN_FAILED"
        else:
            if not source_ready:
                blockers.append(source_reason)
            if qrows.empty:
                blockers.append("NO_COMPLETE_VERIFIED_QUARTERLY_COMPARISON_AVAILABLE")
                quarterly_status = "NO_COMPLETE_VERIFIED_QUARTERLY_COMPARISON_AVAILABLE"
            else:
                qrows = qrows.copy()
                qrows["_date"] = pd.to_datetime(qrows["Quarter_End"], errors="coerce")
                qrows["_published"] = pd.to_datetime(qrows.get("Filing_Published_At", ""), errors="coerce")
                qrows = qrows.sort_values(["_date", "_published"]).drop_duplicates(["Symbol", "Quarter_End"], keep="last")
                latest_row = qrows.iloc[-1]
                latest_date = parse_day(latest_row.get("Quarter_End", ""))
                if latest_date is None:
                    blockers.append("LATEST_QUARTER_DATE_UNVERIFIED")
                elif (date.today() - latest_date).days > FRESHNESS_DAYS:
                    blockers.append("LATEST_QUARTERLY_RESULT_STALE_OVER_180_DAYS")

                if not truthy(latest_row.get("Fundamentals_Complete", "")):
                    blockers.append("LATEST_QUARTER_MISSING_ONE_OR_MORE_OF_SIX_CORE_FIELDS")

                for field in CORE_FIELDS:
                    value = num(latest_row.get(field))
                    if value is None:
                        blockers.append("LATEST_QUARTER_MISSING_" + field)
                for metric, (value_field, qoq_field, yoy_field) in GROWTH_FIELDS.items():
                    current = num(latest_row.get(value_field))
                    qoq = num(latest_row.get(qoq_field))
                    yoy = num(latest_row.get(yoy_field))
                    if current is None or current <= 0:
                        blockers.append(f"LATEST_QUARTER_{metric.upper()}_NOT_POSITIVE")
                    if qoq is None:
                        blockers.append(f"LATEST_QUARTER_{metric.upper()}_QOQ_COMPARISON_UNAVAILABLE")
                    if yoy is None:
                        blockers.append(f"LATEST_QUARTER_{metric.upper()}_YOY_COMPARISON_UNAVAILABLE")
                    elif yoy <= 0:
                        blockers.append(f"LATEST_QUARTER_{metric.upper()}_YOY_GROWTH_NOT_POSITIVE")

                rev = num(latest_row.get("Revenue_Operations_Cr"))
                profit = num(latest_row.get("Net_Profit_Cr"))
                npm = profit / rev * 100.0 if rev is not None and profit is not None and rev > 0 else None
                revenue_yoy = num(latest_row.get("Revenue_YoY_Change_Pct"))
                profit_yoy = num(latest_row.get("Net_Profit_YoY_Change_Pct"))
                margin_yoy = None
                if rev is not None and profit is not None and rev > 0 and revenue_yoy is not None and profit_yoy is not None and (1 + revenue_yoy / 100.0) > 0:
                    prior_rev = rev / (1 + revenue_yoy / 100.0)
                    if (1 + profit_yoy / 100.0) > 0 and prior_rev > 0:
                        prior_profit = profit / (1 + profit_yoy / 100.0)
                        margin_yoy = prior_profit / prior_rev * 100.0
                margin_yoy_delta = npm - margin_yoy if npm is not None and margin_yoy is not None else None
                if npm is None or npm <= 0:
                    blockers.append("LATEST_QUARTER_NET_PROFIT_MARGIN_NOT_POSITIVE")
                if margin_yoy_delta is None:
                    blockers.append("LATEST_QUARTER_YOY_MARGIN_COMPARISON_UNAVAILABLE")
                elif margin_yoy_delta < -2.0:
                    blockers.append("LATEST_QUARTER_NET_PROFIT_MARGIN_DOWN_MORE_THAN_2_PCT_POINTS_YOY")

                last3 = latest_three_quarters(qrows)
                if len(last3) < 3:
                    blockers.append("THREE_CONSECUTIVE_QUARTERS_NEEDED_TO_CHECK_CONTINUOUS_DECLINE")
                else:
                    dates = [parse_day(r.get("Quarter_End", "")) for r in last3]
                    for d1, d2 in zip(dates, dates[1:]):
                        if d1 is None or d2 is None or not 70 <= (d2 - d1).days <= 115:
                            blockers.append("CONTIGUOUS_QUARTER_HISTORY_NOT_AVAILABLE")
                            break
                    for metric, (value_field, _, _) in GROWTH_FIELDS.items():
                        vals = [num(r.get(value_field)) for r in last3]
                        if all(v is not None for v in vals) and vals[2] < vals[1] < vals[0]:
                            blockers.append(f"CONTINUOUS_QOQ_DECLINE_IN_{metric.upper()}")
                quarterly_status = "CHECKED" if not any(x.startswith("LATEST_QUARTER") or x.startswith("CONTINUOUS") or x.startswith("THREE_CONSECUTIVE") for x in blockers) else "FAIL"

        result = {
            "Symbol": sym,
            "Company": company,
            "Annual_Fundamental_Criteria_Pass": annual_pass,
            "Quarterly_Verification_Status": quarterly_status,
            "Latest_Quarter_End": str(latest_row.get("Quarter_End", "")) if latest_row is not None else "",
            "Latest_Quarter_Revenue_Cr": num(latest_row.get("Revenue_Operations_Cr")) if latest_row is not None else None,
            "Latest_Quarter_Net_Profit_Cr": num(latest_row.get("Net_Profit_Cr")) if latest_row is not None else None,
            "Latest_Quarter_EPS_Rs": num(latest_row.get("Basic_EPS_Rs")) if latest_row is not None else None,
            "Latest_Quarter_Assets_Cr": num(latest_row.get("Total_Assets_Cr")) if latest_row is not None else None,
            "Latest_Quarter_Equity_Cr": num(latest_row.get("Total_Equity_Cr")) if latest_row is not None else None,
            "Latest_Quarter_Borrowings_Cr": num(latest_row.get("Total_Borrowings_Cr")) if latest_row is not None else None,
            "Revenue_QoQ_Change_Pct": num(latest_row.get("Revenue_QoQ_Change_Pct")) if latest_row is not None else None,
            "Net_Profit_QoQ_Change_Pct": num(latest_row.get("Net_Profit_QoQ_Change_Pct")) if latest_row is not None else None,
            "EPS_QoQ_Change_Pct": num(latest_row.get("EPS_QoQ_Change_Pct")) if latest_row is not None else None,
            "Revenue_YoY_Change_Pct": num(latest_row.get("Revenue_YoY_Change_Pct")) if latest_row is not None else None,
            "Net_Profit_YoY_Change_Pct": num(latest_row.get("Net_Profit_YoY_Change_Pct")) if latest_row is not None else None,
            "EPS_YoY_Change_Pct": num(latest_row.get("EPS_YoY_Change_Pct")) if latest_row is not None else None,
            "Latest_Quarter_Net_Profit_Margin_Pct": (
                num(latest_row.get("Net_Profit_Cr")) / num(latest_row.get("Revenue_Operations_Cr")) * 100.0
                if latest_row is not None and num(latest_row.get("Revenue_Operations_Cr")) not in (None, 0)
                and num(latest_row.get("Net_Profit_Cr")) is not None else None
            ),
            "YoY_Net_Profit_Margin_Change_Pct_Points": margin_yoy_delta if latest_row is not None and annual_pass and not qrows.empty else None,
            "Final_Fundamental_Criteria_Pass": not blockers,
            "Final_Failure_Reasons": "|".join(dict.fromkeys(blockers)),
            "Latest_Quarter_Source_URL": str(latest_row.get("Source_URL", "")) if latest_row is not None else "",
        }
        if not blockers:
            short_rows.append(result)
        else:
            excluded_rows.append(result)

    shortlist = pd.DataFrame(short_rows, columns=list(result) if len(annual) else ["Symbol", "Final_Fundamental_Criteria_Pass"])
    exclusions = pd.DataFrame(excluded_rows)
    shortlist.to_csv(SHORTLIST_FILE, index=False, na_rep="")
    exclusions.to_csv(EXCLUSIONS_FILE, index=False, na_rep="")
    summary = pd.DataFrame([{
        "Annual_Screen_Companies": len(annual),
        "Annual_Pass_Companies": int(annual["Full_Fundamental_Criteria_Pass"].map(truthy).sum()),
        "Companies_With_Quarterly_Data": len(q_by_symbol),
        "Quarterly_Source_Gate_Pass": source_ready,
        "Quarterly_Source_Gate_Reason": source_reason,
        "Final_Shortlist_Count": len(shortlist),
        "Excluded_After_Annual_And_Quarterly_Checks": len(exclusions),
        "Report_Generated_On": date.today().isoformat(),
        "Criteria_Note": "Strict screen; missing/unverified values are never coerced to zero; sector-specialist issuers remain blocked until specific metrics are available.",
    }])
    summary.to_csv(SUMMARY_FILE, index=False, na_rep="")
    print(f"Final shortlist: {len(shortlist)}; exclusions with reasons: {len(exclusions)}")
    print(f"Saved {SHORTLIST_FILE.name}, {EXCLUSIONS_FILE.name}, {SUMMARY_FILE.name}")


if __name__ == "__main__":
    main()


