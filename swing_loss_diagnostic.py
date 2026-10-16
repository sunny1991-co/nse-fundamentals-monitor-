"""Read-only diagnosis of negative fee-aware equity swing backtests.

This does not tune entry/exit parameters, select winners or place orders.
Outlier flags suggest manual source review, not proof of bad source data.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median

import pandas as pd

from research_monitor.groww_delivery_costs import simulate_delivery
from research_monitor.history import read_history
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status
from swing_delivery_fee_research import BUILDERS, END_DATE, _signals_for


def _describe(trades):
    completed = [t for t in trades if t["Reason"] != "sample_end"]
    if not completed:
        return {
            "completed_trades": 0, "wins": 0, "losses": 0,
            "mean_net_trade_return": None, "mean_gross_filled_trade_return": None,
            "mean_fee_drag_percentage_points": None,
            "mean_holding_sessions": None, "by_exit_reason": {},
        }
    by_reason = {}
    for reason in sorted({t["Reason"] for t in completed}):
        selected = [t for t in completed if t["Reason"] == reason]
        by_reason[reason] = {
            "count": len(selected),
            "mean_net_return": sum(float(t["Return"]) for t in selected)/len(selected),
            "wins": sum(float(t["Return"]) > 0 for t in selected),
            "losses": sum(float(t["Return"]) < 0 for t in selected),
        }
    gross = [
        (float(t["Sell_Turnover_Rs"]) - float(t["Buy_Turnover_Rs"]))
        / float(t["Buy_Turnover_Rs"]) for t in completed
    ]
    net = [float(t["Return"]) for t in completed]
    return {
        "completed_trades": len(completed),
        "wins": sum(r > 0 for r in net),
        "losses": sum(r < 0 for r in net),
        "breakeven": sum(r == 0 for r in net),
        "median_net_trade_return": median(net),
        "mean_net_trade_return": sum(net)/len(net),
        "mean_gross_filled_trade_return": sum(gross)/len(gross),
        "mean_fee_drag_percentage_points": (
            100*sum(a-b for a,b in zip(gross,net))/len(net)
        ),
        "mean_holding_sessions": (
            sum(int(t["Held_Sessions"]) for t in completed)/len(completed)
        ),
        "by_exit_reason": by_reason,
    }


def diagnose(raw, digest, limit=100, top=5, selected=None):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("LIMIT_1_TO_100_REQUIRED")
    if not isinstance(top, int) or not 1 <= top <= 20:
        raise ValueError("TOP_1_TO_20_REQUIRED")
    selected = selected or [name for name,_ in BUILDERS]
    known = {name: builder for name,builder in BUILDERS}
    if not selected or len(set(selected)) != len(selected) or any(
        name not in known for name in selected
    ):
        raise ValueError("UNKNOWN_OR_DUPLICATE_STRATEGY")
    frozen = raw[raw.Date <= pd.Timestamp(END_DATE)]
    groups = {symbol: bars for symbol,bars in frozen.groupby("Symbol")
              if len(bars) >= 1260}
    symbols = sorted(groups, key=lambda s: hashlib.sha256(s.encode()).hexdigest())[:limit]
    detail = {name: [] for name in selected}
    flagged_symbols = []
    for symbol in symbols:
        bars = groups[symbol]
        # Extreme historical open/close jumps can be genuine or reflect
        # adjusted data/corporate actions. They require independent review.
        close_jump = bars.Close.pct_change().abs()
        open_jump = (bars.Open/bars.Close.shift(1)-1).abs()
        count = int(((close_jump > .35) | (open_jump > .35)).sum())
        if count:
            flagged_symbols.append({"symbol": symbol, "extreme_jump_days": count})
        cut = 220+int((len(bars)-220)*.7)
        for name in selected:
            d = _signals_for(name,known[name],bars)
            _, ledger = simulate_delivery(d, cut, len(d), capital=100000,
                                          slip=.001, stress_multiplier=1.0)
            for t in ledger:
                detail[name].append({"symbol": symbol, **t})
    summaries = {}
    for name in selected:
        ledger = detail[name]
        complete = [t for t in ledger if t["Reason"] != "sample_end"]
        desc = _describe(ledger)
        desc["sample_end_liquidations_not_in_completed_metrics"] = (
            len(ledger)-len(complete)
        )
        desc["worst_completed_trades"] = sorted(
            [{
                "symbol": t["symbol"],
                "entry_date": t["Entry_Date"],
                "exit_date": t["Exit_Date"],
                "exit_reason": t["Reason"],
                "net_return_pct": round(100*float(t["Return"]), 2),
                "charges_rs": round(float(t["All_Trading_Charges_Rs"]), 2),
                "gross_filled_return_pct": round(
                    100*(float(t["Sell_Turnover_Rs"])/float(t["Buy_Turnover_Rs"])-1),2
                ),
                "held_sessions": int(t["Held_Sessions"]),
            } for t in complete], key=lambda t:(t["net_return_pct"],t["symbol"])
        )[:top]
        summaries[name] = desc
    return {
        "scope": "LOSS_ATTRIBUTION_NO_OPTIMIZATION_OR_STRATEGY_APPROVAL",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_sha256": digest,
        "sample_symbols": symbols,
        "eligible_universe": len(groups),
        "analysed_stocks": len(symbols),
        "summary": summaries,
        "potential_extreme_jump_symbols_requiring_independent_review":
            sorted(flagged_symbols,key=lambda x:(-x["extreme_jump_days"],x["symbol"]))[:20],
        "extreme_jump_threshold": "abs(open/previous-close-1) or abs(close/previous-close-1) > 35%",
        "limitations": [
            "A >35% daily jump is not proof of a data error or corporate action.",
            "Gross filled returns include modeled slippage; fee drag is gross minus net.",
            "Historical sample is already inspected and not independent validation.",
            "No live orders, recommendations or active strategy changes.",
        ],
        "live_eligible": False,
        "orders": "DISABLED",
    }


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit",type=int,default=100)
    ap.add_argument("--top",type=int,default=5)
    args=ap.parse_args()
    if not 1 <= args.limit <= 100 or not 1 <= args.top <= 20:
        ap.error("--limit must be 1..100 and --top must be 1..20")
    root=data_root()
    raw,digest=read_history(root)
    result=diagnose(raw,digest,args.limit,args.top)
    out=root/"runtime"/"experiments"/"loss-diagnostic-v1"/"report.json"
    atomic_status(out,result)
    print(json.dumps({
        "scope": result["scope"],
        "analysed_stocks": result["analysed_stocks"],
        "summary": result["summary"],
        "potential_extreme_jump_symbols_requiring_independent_review":
            result["potential_extreme_jump_symbols_requiring_independent_review"],
        "live_eligible": False,
    },indent=2))
    print("Private loss diagnostic:",out)


if __name__=="__main__":
    main()
