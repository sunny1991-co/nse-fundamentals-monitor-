"""Descriptive, exact-date NIFTY 50/100 PRICE RETURN market context.

Isolated research ONLY. Not gross TRI, not a stock-strategy benchmark,
not a live quote or broker signal, and never a reason to unlock live orders.
No network traffic and no third-party packages.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import math
import os
from pathlib import Path

from nifty_price_return_import import HEADER, SUPPORTED, TYPE, atomic_write, csv_bytes

VERSION = "nifty-pr-market-context-v1"
OUTPUT = "runtime/experiments/nifty-pr-market-context-v1/report.json"
WINDOWS = (21, 63, 126, 252)
MIN_COMMON_SESSIONS = max(WINDOWS) + 1


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def read_index(root, index):
    """Read only the importer-specific, hash-attested research PR data."""
    if index not in SUPPORTED:
        raise ValueError("PR_CONTEXT_UNSUPPORTED_INDEX")
    stem = SUPPORTED[index]
    csv_path, meta_path = Path(root)/(stem + ".csv"), Path(root)/(stem + "_metadata.json")
    try:
        blob = csv_path.read_bytes()
        receipt = json.loads(meta_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, UnicodeError):
        raise ValueError("PR_CONTEXT_INPUT_MISSING_OR_BAD_RECEIPT") from None
    if not isinstance(receipt, dict) or not (
        receipt.get("type") == TYPE
        and receipt.get("return_kind") == "PRICE_RETURN_NOT_TRI"
        and receipt.get("index_name") == index
        and receipt.get("operator_source_checked") is False
        and receipt.get("eligible_for_tri_strategy_benchmark") is False
        and receipt.get("live_orders") == "DISABLED"
        and receipt.get("sha256") == digest(blob)
    ):
        raise ValueError("PR_CONTEXT_SOURCE_INTEGRITY_INVALID")
    try:
        reader = csv.DictReader(io.StringIO(blob.decode("utf-8-sig"), newline=""))
        if reader.fieldnames != HEADER:
            raise ValueError("PR_CONTEXT_SCHEMA_INVALID")
        rows = {}
        previous_day = None
        for record in reader:
            if None in record or any(value is None for value in record.values()):
                raise ValueError("PR_CONTEXT_BAD_ROW")
            if record["Index Name"] != index:
                raise ValueError("PR_CONTEXT_WRONG_INDEX")
            if len(record["Date"]) != 10:
                raise ValueError("PR_CONTEXT_DATE_INVALID")
            try:
                d = date.fromisoformat(record["Date"])
                if d.isoformat() != record["Date"]:
                    raise ValueError("PR_CONTEXT_DATE_INVALID")
                o, h, l, c = (Decimal(record[key]) for key in HEADER[2:])
            except (ValueError, InvalidOperation, TypeError):
                raise ValueError("PR_CONTEXT_BAD_ROW") from None
            if (not all(x.is_finite() and 0 < x < 10**9 for x in (o,h,l,c))
                    or h < max(o, c, l) or l > min(o, c, h)):
                raise ValueError("PR_CONTEXT_BAD_OHLC")
            if previous_day is not None and d <= previous_day:
                raise ValueError("PR_CONTEXT_DATES_NOT_INCREASING")
            rows[d] = (o, h, l, c)
            previous_day = d
        if (not rows or len(rows) != receipt.get("rows")
                or min(rows).isoformat() != receipt.get("first_date")
                or max(rows).isoformat() != receipt.get("last_date")
                or csv_bytes(rows, index) != blob):
            raise ValueError("PR_CONTEXT_CANONICAL_MISMATCH")
    except UnicodeError:
        raise ValueError("PR_CONTEXT_ENCODING_INVALID") from None
    return rows, receipt["sha256"]


def ratio_pct(new, old):
    """Percentage-point rate; never round before a comparison."""
    return round(float((new / old - 1) * 100), 4)


def describe(close):
    latest = close[-1]
    trailing = close[-200:]
    ma50 = sum(close[-50:]) / Decimal(50)
    ma200 = sum(trailing) / Decimal(200)
    last_252 = close[-252:]
    peak = last_252[0]
    max_dd = Decimal(0)
    for p in last_252:
        peak = max(peak, p)
        max_dd = min(max_dd, p / peak - 1)
    returns = {str(window) + "_session_pct": ratio_pct(latest, close[-(window+1)])
               for window in WINDOWS}
    is_positive = latest > ma200 and ma50 > ma200 and close[-1] > close[-64]
    is_negative = latest < ma200 and ma50 < ma200 and close[-1] < close[-64]
    return {
        "close": round(float(latest), 4),
        "returns": returns,
        "sma50": round(float(ma50), 4),
        "sma200": round(float(ma200), 4),
        "above_sma200": latest > ma200,
        "sma50_above_sma200": ma50 > ma200,
        "drawdown_from_252_session_high_pct":
            round(float((latest / max(last_252) - 1) * 100), 4),
        "max_252_session_close_to_close_drawdown_pct":
            round(float(max_dd * 100), 4),
        "historical_price_state":
            "POSITIVE_TREND" if is_positive else
            "NEGATIVE_TREND" if is_negative else "MIXED",
    }


def compare(root, *, as_of=None, today=None):
    """Compare ONLY the dates present in both local PR series. No filling."""
    today = today or datetime.now(timezone.utc).date()
    if as_of is not None:
        if not isinstance(as_of, date) or isinstance(as_of, datetime) or as_of > today:
            raise ValueError("PR_CONTEXT_AS_OF_INVALID")
    fifty, fifty_hash = read_index(root, "NIFTY 50")
    hundred, hundred_hash = read_index(root, "NIFTY 100")
    cutoff = as_of or today
    dates50 = {d for d in fifty if d <= cutoff}
    dates100 = {d for d in hundred if d <= cutoff}
    dates = sorted(dates50 & dates100)
    if len(dates) < MIN_COMMON_SESSIONS:
        raise ValueError("PR_CONTEXT_253_COMMON_SESSIONS_REQUIRED")
    unmatched50 = len(dates50 - dates100)
    unmatched100 = len(dates100 - dates50)
    # Do not imply any missing session was a market holiday.
    if unmatched50 > 10 or unmatched100 > 10:
        raise ValueError("PR_CONTEXT_TOO_MANY_UNMATCHED_DATES")
    close50 = [fifty[d][3] for d in dates]
    close100 = [hundred[d][3] for d in dates]
    stats50 = describe(close50)
    stats100 = describe(close100)
    excess = {
        str(k) + "_session_nifty100_minus_nifty50_pct_points":
        round(stats100["returns"][str(k)+"_session_pct"] -
              stats50["returns"][str(k)+"_session_pct"], 4)
        for k in WINDOWS
    }
    return {
        "scope": "NIFTY_50_100_PRICE_RETURN_DESCRIPTIVE_RESEARCH_ONLY",
        "state": "PR_DUAL_INDEX_CONTEXT_READY_NOT_TRADING_VALIDATED",
        "version": VERSION,
        "requested_as_of_date": cutoff.isoformat(),
        "as_of_last_exact_common_date": dates[-1].isoformat(),
        "source_last_dates": {
            "NIFTY 50": max(fifty).isoformat(),
            "NIFTY 100": max(hundred).isoformat(),
        },
        "as_of_source_latest_dates": {
            "NIFTY 50": max(dates50).isoformat(),
            "NIFTY 100": max(dates100).isoformat(),
        },
        "first_exact_common_date": dates[0].isoformat(),
        "exact_common_session_count": len(dates),
        "unmatched_session_dates": {
            "NIFTY 50 only": unmatched50,
            "NIFTY 100 only": unmatched100,
        },
        "inputs_sha256": {"NIFTY 50": fifty_hash, "NIFTY 100": hundred_hash},
        "market_context": {"NIFTY 50": stats50, "NIFTY 100": stats100},
        "relative_index_price_returns": excess,
        "data_fresh_for_live_quote": False,
        "benchmark_kind": "PRICE_RETURN_EX_DIVIDENDS_NOT_GROSS_TRI",
        "operator_source_checked": False,
        "historical_regime_not_entry_signal": True,
        "stock_strategy_backtest_conducted": False,
        "fundamental_screen_pass_claimed": False,
        "real_profitability_verified": False,
        "tri_required_strategies_unblocked": False,
        "orders": "DISABLED",
        "limitations": [
            "Both local PR CSVs were supplied by owner; origin is not independently verified.",
            "Index price levels exclude dividends, so not gross TRI or investor total returns.",
            "Returns use exact common observation dates with no holiday fabrication or forward filling.",
            "Closing-price 50/200-SMA states are descriptive and not next-session predictions.",
            "No brokerage, tax, slippage or funding cost is included for either index.",
            "This report cannot backtest or validate NSE EQ strategies, select stock entries or authorize orders.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", help="YYYY-MM-DD historical cutoff, default latest available")
    args = parser.parse_args()
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else None
        root = Path(os.environ.get("NSE_DATA_DIR") or
                    str(Path.home() / "nse-fundamentals-data")).expanduser()
        if not root.is_absolute():
            raise ValueError("PR_CONTEXT_DATA_DIR_ABSOLUTE_REQUIRED")
        result = compare(root, as_of=as_of)
        output = root/OUTPUT
        atomic_write(output, (json.dumps(result, indent=2) + "\n").encode("utf-8"))
        result = {
            "state": result["state"],
            "as_of": result["as_of_last_exact_common_date"],
            "matched_sessions": result["exact_common_session_count"],
            "NIFTY_50": result["market_context"]["NIFTY 50"],
            "NIFTY_100": result["market_context"]["NIFTY 100"],
            "relative_index_price_returns": result["relative_index_price_returns"],
            "report": str(output),
            "tri_required_strategies_unblocked": False,
            "orders": "DISABLED",
        }
    except (ValueError, OSError, TypeError) as exc:
        known = str(exc)
        result = {
            "state": "PR_DUAL_INDEX_CONTEXT_BLOCKED",
            "reason_code": known if known.startswith("PR_CONTEXT_") else
                           "PR_CONTEXT_INPUT_INVALID",
            "tri_required_strategies_unblocked": False,
            "orders": "DISABLED",
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
