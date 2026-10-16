"""Generate provisional fast-research swing candidates.

Uses the lower-confidence two-year fundamental screen plus the same strict
technical holdout evidence and current EOD signal. Output is separate from the
strict candidate file and never enables SMS, live alerts, or orders.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import pandas as pd

from research_monitor.paths import data_root
from swing_backtest import load_market, features, VERSION, MAX_HOLD

ROOT = data_root()
FUND = ROOT / "fast_fundamental_shortlist.csv"
SUMMARY = ROOT / "fast_fundamental_screen_summary.csv"
BACKTEST = ROOT / "swing_backtest_report.csv"
OUTPUT = ROOT / "fast_swing_research_candidates.csv"


def truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "pass"}


def main() -> None:
    columns = [
        "Symbol", "Signal_Date", "Entry_Min", "Entry_Max_No_Chase", "Stop_Loss",
        "Target_1_At_Min_Entry", "Target_2_At_Min_Entry", "Holdout_Trades",
        "Win_Rate", "Mean_Net_Trade_Return", "Max_Drawdown", "Strategy",
        "Research_Scope", "Status",
    ]
    OUTPUT.unlink(missing_ok=True)

    for path in (FUND, SUMMARY, BACKTEST, ROOT / "market_ohlcv.csv", ROOT / "market_data_metadata.json"):
        if not path.is_file() or path.stat().st_size == 0:
            pd.DataFrame(columns=columns).to_csv(OUTPUT, index=False)
            print("Fast swing research waiting for verified inputs.")
            return

    summary = pd.read_csv(SUMMARY, dtype=str, keep_default_na=False)
    if summary.empty or summary.iloc[-1].get("Status") != "PROVISIONAL_SCREEN_COMPLETE":
        raise ValueError("Fresh provisional fundamental screen required")
    stamp = pd.to_datetime(summary.iloc[-1].get("Report_Generated_On", ""), errors="raise")
    today = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).normalize()
    if pd.isna(stamp) or not 0 <= (today - stamp).days <= 1:
        raise ValueError("Provisional fundamental screen is stale")

    fund = pd.read_csv(FUND, dtype=str, keep_default_na=False)
    approved = set(
        fund.loc[
            fund.get("Fast_Fundamental_Pass", pd.Series([], dtype=str)).astype(str).str.lower().eq("true"),
            "Symbol",
        ].astype(str).str.strip().str.upper()
    )
    if not approved:
        pd.DataFrame(columns=columns).to_csv(OUTPUT, index=False)
        print("No provisional fundamental passes; no fast swing candidates.")
        return

    raw, digest = load_market()
    report = pd.read_csv(BACKTEST, dtype=str, keep_default_na=False)
    rows = []
    for symbol, bars in raw.groupby("Symbol"):
        symbol = str(symbol).strip().upper()
        if symbol not in approved:
            continue
        evidence = report[report["Symbol"].astype(str).str.upper().eq(symbol)]
        if evidence.empty:
            continue
        e = evidence.iloc[0]
        if not truthy(e.get("Backtest_Pass")):
            continue
        if e.get("Data_SHA256") != digest or e.get("Strategy") != VERSION:
            continue

        d = features(bars)
        if d.empty:
            continue
        last = d.iloc[-1]
        if not bool(last.Signal) or not 0 <= (today - last.Date).days <= 4:
            continue

        entry = float(last.Close)
        stop = float(last.Stop)
        if not 0 < stop < entry:
            continue
        rows.append({
            "Symbol": symbol,
            "Signal_Date": str(last.Date.date()),
            "Entry_Min": round(entry, 2),
            "Entry_Max_No_Chase": round(entry * 1.01, 2),
            "Stop_Loss": round(stop, 2),
            "Target_1_At_Min_Entry": round(entry + 2 * (entry - stop), 2),
            "Target_2_At_Min_Entry": round(entry + 3 * (entry - stop), 2),
            "Holdout_Trades": e.get("Trades", ""),
            "Win_Rate": e.get("Win_Rate", ""),
            "Mean_Net_Trade_Return": e.get("Mean_Return", ""),
            "Max_Drawdown": e.get("Max_Drawdown", ""),
            "Strategy": VERSION,
            "Research_Scope": "PROVISIONAL_LOWER_CONFIDENCE_RESEARCH_ONLY",
            "Status": (
                "EOD conditional research only; two-year fundamentals; no sector/4Y/FCF validation; "
                "not a calibrated probability; no order or SMS"
            ),
        })

    pd.DataFrame(rows, columns=columns).to_csv(OUTPUT, index=False)
    print(f"{len(rows)} provisional fast-research candidates; strict candidate file unchanged.")


if __name__ == "__main__":
    main()
