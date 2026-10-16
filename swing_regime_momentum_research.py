"""Original NSE swing hypothesis vs VCP: market breadth + relative momentum.

Standalone, read-only exploratory runner. Signals are close-confirmed and all
execution is simulated at a subsequent session open. No live candidate, SMS
or order pathway is connected.

FROZEN RULES: The first historical run cannot be used to retune these rules.
Prices sampled from TODAY'S surviving dataset are not point-in-time constituents.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd

from research_monitor.groww_delivery_costs import simulate_delivery
from research_monitor.history import read_history
from research_monitor.paths import CODE_ROOT, data_root
from research_monitor.regime_momentum_rules import (
    PARAMETERS, STRATEGY_ID, make_market_context, regime_features,
)
from research_monitor.schedule import atomic_status
from research_monitor.seven_strategy_rules import signals
from swing_backtest import features
from swing_custom_strategy_research import END_DATE

VERSION = "regime-momentum-research-v1"
COMPARATOR = "vcp-breakout-v1"
CAPITAL = 100_000.0
RISK = .01
CODE_FILES = (
    "swing_regime_momentum_research.py",
    "research_monitor/regime_momentum_rules.py",
    "research_monitor/groww_delivery_costs.py",
    "research_monitor/seven_strategy_rules.py",
    "swing_backtest.py",
    "swing_strategy.py",
)
PROTOCOL = {
    "version": VERSION,
    "design_date": "2026-10-16",
    "cutoff": END_DATE,
    "minimum_bars": 1260,
    "historical_window": "last 30% after 220-session indicator warmup; ALREADY INSPECTED",
    "sample_order": "SHA256(symbol), ascending, not performance-ranked",
    "strategy_id": STRATEGY_ID,
    "comparator": COMPARATOR,
    "parameters": PARAMETERS,
    "context": (
        "same-session equal-weight breadth above EMA200 and 126-session "
        "momentum percentile among currently history-eligible survivors"
    ),
    "execution": (
        "next-session open with one-percent fill guard; original stop-first, "
        "partial 2R/3R exits and 100-session time exit"
    ),
    "initial_capital_per_independent_stock_account": CAPITAL,
    "risk_fraction_of_pre_trade_cash": RISK,
    "normal_slippage_per_side": 0.001,
    "stress_slippage_per_side": 0.002,
    "stress_multiplier_on_estimated_groww_delivery_fees": 2,
    "broker_orders": "DISABLED",
}


def _completed_metrics(trades):
    completed = [t for t in trades if t.get("Reason") != "sample_end"]
    net = [float(t["Return"]) for t in completed]
    capital_returns = [float(t["Return_On_Pre_Trade_Capital"]) for t in completed]
    wins = sum(r > 0 for r in net)
    losses = -sum(r for r in net if r < 0)
    gains = sum(r for r in net if r > 0)
    return {
        "count": len(completed),
        "wins": wins,
        "losses": sum(r < 0 for r in net),
        "mean_net_position_return": sum(net)/len(net) if net else None,
        "mean_net_pre_trade_cash_return": (
            sum(capital_returns)/len(capital_returns) if capital_returns else None
        ),
        "net_profit_factor": gains/losses if losses > 0 else None,
        "stop_exits": sum(t["Reason"] == "stop" for t in completed),
        "gap_below_stop_exits": sum(t.get("Gap_Below_Stop") is True for t in completed),
        "risk_budget_breaches": sum(
            t.get("Risk_Budget_Exceeded_On_Exit") is True for t in completed
        ),
    }


def compare(raw, digest, limit=30):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("LIMIT_MUST_BE_1_TO_100")
    frozen = raw.loc[raw.Date <= pd.Timestamp(END_DATE)]
    groups = {
        symbol: group for symbol, group in frozen.groupby("Symbol")
        if len(group) >= PROTOCOL["minimum_bars"]
    }
    symbols = sorted(
        groups, key=lambda symbol: hashlib.sha256(symbol.encode()).hexdigest()
    )[:limit]

    # Market context is constructed on ALL qualified price histories, NOT only
    # the 100 reported stocks. Current survivorship and data-source accuracy
    # are still limitations. Neither market nor rank is forward-filled.
    breadth, ranks = make_market_context(groups)
    pooled = {
        name: {
            scenario: {"trades": [], "stock_results": [], "signals": 0}
            for scenario in ("normal", "double_cost_stress")
        }
        for name in (STRATEGY_ID, COMPARATOR)
    }
    stock_observations = []
    for symbol in symbols:
        bars = groups[symbol]
        start = 220 + int((len(bars)-220)*.7)
        ours = regime_features(bars, symbol, breadth, ranks)
        existing = features(bars)
        existing = existing.assign(Signal=signals(existing, COMPARATOR))
        for name, d in ((STRATEGY_ID, ours), (COMPARATOR, existing)):
            signal_count = int(d.Signal.iloc[start-1:-1].sum())
            for scenario, cost_multiplier, slippage in (
                ("normal", 1., .001), ("double_cost_stress", 2., .002),
            ):
                result, ledger = simulate_delivery(
                    d, start, len(d), capital=CAPITAL, risk_fraction=RISK,
                    slip=slippage, stress_multiplier=cost_multiplier,
                )
                p = pooled[name][scenario]
                p["trades"].extend(ledger)
                p["signals"] += signal_count
                p["stock_results"].append({
                    "symbol": symbol,
                    "completed_trades": result["Completed_Trades"],
                    "forced_sample_end": sum(
                        t["Reason"] == "sample_end" for t in ledger
                    ),
                    "max_drawdown": float(result["Max_Drawdown"]),
                    "charges_rs": float(result["Estimated_All_Trading_Charges_Rs"]),
                    "marked_account_return": (
                        float(result["Final_Marked_Capital_Rs"])/CAPITAL-1
                    ),
                })
                if scenario == "normal":
                    stats = _completed_metrics(ledger)
                    stock_observations.append({
                        "symbol": symbol, "strategy": name,
                        "historical_start": str(d.Date.iloc[start].date()),
                        "historical_end": str(d.Date.iloc[-1].date()),
                        "signals": signal_count,
                        "completed_trades": stats["count"],
                        "mean_net_position_return":
                            stats["mean_net_position_return"],
                        "mean_net_pre_trade_cash_return":
                            stats["mean_net_pre_trade_cash_return"],
                    })

    summaries = {}
    for name, scenarios in pooled.items():
        summaries[name] = {}
        for scenario, p in scenarios.items():
            stats = _completed_metrics(p["trades"])
            reports = p["stock_results"]
            summaries[name][scenario] = {
                "stocks": len(reports),
                "signal_opportunities": p["signals"],
                "completed_trades": stats["count"],
                "completed_wins": stats["wins"],
                "completed_losses": stats["losses"],
                "mean_net_position_return": stats["mean_net_position_return"],
                "mean_net_pre_trade_cash_return":
                    stats["mean_net_pre_trade_cash_return"],
                "completed_net_profit_factor": stats["net_profit_factor"],
                "stop_exits": stats["stop_exits"],
                "gap_below_stop_exits": stats["gap_below_stop_exits"],
                "risk_budget_breaches": stats["risk_budget_breaches"],
                "sample_end_liquidations": sum(
                    row["forced_sample_end"] for row in reports
                ),
                "worst_single_stock_max_drawdown": max(
                    (row["max_drawdown"] for row in reports), default=None
                ),
                "fees_sum_across_independent_stock_accounts_rs": sum(
                    row["charges_rs"] for row in reports
                ),
            }

    code_sha = hashlib.sha256(b"".join(
        (CODE_ROOT / filename).read_bytes() for filename in CODE_FILES
    )).hexdigest()
    return {
        "scope": "NEW_REGIME_MOMENTUM_HYPOTHESIS_EXPLORATORY_NOT_VALIDATED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "protocol_sha256": hashlib.sha256(
            json.dumps(PROTOCOL,sort_keys=True).encode()
        ).hexdigest(),
        "data_sha256": digest,
        "implementation_sha256": code_sha,
        "eligible_universe": len(groups),
        "historical_sample_symbols": symbols,
        "summary": summaries,
        "stock_observations": stock_observations,
        "verified_profitable_strategies": 0,
        "live_eligible": False,
        "trade_execution": "DISABLED",
        "approved_for_current_signals": False,
        "validation_status": "NOT_VALIDATED",
        "limitations": [
            "Entry and parameters defined after previous sample had already been inspected.",
            "Historical universe of today's surviving firms is not point-in-time correct.",
            "Cross-sectional rank and breadth have as-of-date prices, but survivor bias remains.",
            "A 1% modeled initial-stop budget cannot prevent >1% losses from gaps.",
            "Fees and slippage are estimates; real execution, taxes and past tariffs differ.",
            "Independent stock subaccounts are not a realistic shared-capital portfolio.",
            "Point-in-time audited fundamentals and independent forward paper trades "
            "remain mandatory before trading is considered.",
        ],
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit",type=int,default=30)
    args=parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be 1..100")
    root=data_root()
    raw,digest=read_history(root)
    result=compare(raw,digest,limit=args.limit)
    path=root/"runtime"/"experiments"/VERSION/"comparison.json"
    atomic_status(path,result)
    print(json.dumps({
        "scope": result["scope"],
        "eligible_universe": result["eligible_universe"],
        "sample_stocks": len(result["historical_sample_symbols"]),
        "summary": result["summary"],
        "validation_status": result["validation_status"],
        "live_eligible": False,
    },indent=2))
    print("Private research report:",path)


if __name__=="__main__":
    main()
