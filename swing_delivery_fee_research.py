"""Compare seven distinct price-only swing hypotheses using per-order Groww fees.

RESEARCH ONLY. No strategy selection for live use, alerts, stock tips or orders.
Uses the previously inspected historical sample: retrospective comparisons
cannot validate any newly invented strategy or justify choosing a live Top 3.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from statistics import median

import pandas as pd

from research_monitor.exploratory_ranking import rank_exploratory
from research_monitor.groww_delivery_costs import (
    LAST_REVIEWED, simulate_delivery,
)
from research_monitor.history import read_history
from research_monitor.paths import CODE_ROOT, data_root
from research_monitor.schedule import atomic_status
from research_monitor.seven_strategy_rules import signals
from swing_backtest import VERSION, features
from swing_custom_strategy_research import NAME, custom_features, END_DATE
from swing_pullback_research import (
    PROTOCOL as PULLBACK_PROTOCOL,
    pullback_features,
    completed_trade_stats,
)

# 7 distinct rule sets. The original ema-pullback-v1 signal is identical
# to trend-pullback-research-v1, so avoid counting it as an eighth discovery.
BUILDERS = (
    (VERSION, features),
    ("trend-pullback-research-v1", pullback_features),
    (NAME, custom_features),
    ("trend-following-v1", None),
    ("momentum-breakout-v1", None),
    ("vcp-breakout-v1", None),
    ("mean-reversion-v1", None),
)
STRATEGY_IDS = [x[0] for x in BUILDERS]
VERSION_ID = "groww-delivery-cost-research-v1"
CODE_PATHS = (
    "swing_delivery_fee_research.py",
    "research_monitor/groww_delivery_costs.py",
    "research_monitor/exploratory_ranking.py",
    "research_monitor/seven_strategy_rules.py",
    "swing_backtest.py",
    "swing_strategy.py",
    "swing_pullback_research.py",
    "swing_custom_strategy_research.py",
)
PROTOCOL = {
    "version": VERSION_ID,
    "frozen_design_date": "2026-10-16",
    "exploration_end": END_DATE,
    "hypotheses": STRATEGY_IDS,
    "ema_pullback_alias": "trend-pullback-research-v1",
    "minimum_history_bars": 1260,
    "sample_order": "SHA256(symbol) independent of observed results",
    "window": "last 30% after 220 warmup bars; ALREADY INSPECTED",
    "starting_capital_per_individual_stock_rs": 100000,
    "broker": "Groww standard NSE equity delivery, illustrative 2026-10-16 tariff",
    "fee_tariff_reviewed": LAST_REVIEWED,
    "includes": [
        "Groww brokerage per execution",
        "buy/sell delivery STT, buy stamp duty",
        "NSE transaction, SEBI, IPFT fees",
        "sell-order DP, 18% GST on taxable service charges",
        "slippage and whole-share capital-constrained execution",
    ],
    "excludes": [
        "personal income tax and capital-gains tax",
        "UPI mandate balance special 1% brokerage",
        "MTF interest, penalties, demat/instrument-specific exceptions",
        "actual split fills, intraday exchange rounding and real market depth",
    ],
    "normal_fill_slippage_each_side": 0.001,
    "double_stress_fee_and_slippage_factor": 2,
    "orders": "DISABLED",
    "live_eligible": False,
}


def _signals_for(name, builder, bars):
    if builder is not None:
        return builder(bars)
    d = features(bars)
    return d.assign(Signal=signals(d, name))


def compare(raw, digest, limit=30):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("LIMIT_MUST_BE_1_TO_100")
    frozen = raw.loc[raw.Date <= pd.Timestamp(END_DATE)]
    groups = {
        symbol: bars for symbol, bars in frozen.groupby("Symbol")
        if len(bars) >= PROTOCOL["minimum_history_bars"]
    }
    symbols = sorted(
        groups, key=lambda s: hashlib.sha256(s.encode()).hexdigest()
    )[:limit]
    pool = {
        name: {"normal": [], "stress": [], "symbols": set(), "rows": []}
        for name in STRATEGY_IDS
    }
    observations = []
    for symbol in symbols:
        bars = groups[symbol]
        cut = 220+int((len(bars)-220)*.7)
        for name, builder in BUILDERS:
            d = _signals_for(name, builder, bars)
            stats, trades = simulate_delivery(
                d, cut, len(d), capital=100000,
                slip=.001, stress_multiplier=1.0,
            )
            stress_stats, stress_trades = simulate_delivery(
                d, cut, len(d), capital=100000,
                slip=.002, stress_multiplier=2.0,
            )
            normal_complete = completed_trade_stats(trades)
            stress_complete = completed_trade_stats(stress_trades)
            completed_base = [
                float(t["Return"]) for t in trades
                if t.get("Reason") not in (None, "sample_end")
            ]
            completed_stress = [
                float(t["Return"]) for t in stress_trades
                if t.get("Reason") not in (None, "sample_end")
            ]
            p = pool[name]
            p["normal"].extend(completed_base)
            p["stress"].extend(completed_stress)
            if completed_base:
                p["symbols"].add(symbol)
            entry = {
                "symbol": symbol,
                "strategy": name,
                "window_start": str(d.Date.iloc[cut].date()),
                "window_end": str(d.Date.iloc[-1].date()),
                "signals": int(d.Signal.iloc[cut-1:-1].sum()),
                "completed_trades": normal_complete["count"],
                "completed_net_mean": normal_complete["mean_net_return"],
                "completed_net_profit_factor": normal_complete["profit_factor"],
                "stress_completed_trades": stress_complete["count"],
                "stress_completed_net_mean": stress_complete["mean_net_return"],
                "max_single_stock_drawdown": stats["Max_Drawdown"],
                "normal_charges_rs": stats["Estimated_All_Trading_Charges_Rs"],
                "stress_charges_rs": stress_stats["Estimated_All_Trading_Charges_Rs"],
                "forced_sample_end": stats["Trades"]-stats["Completed_Trades"],
            }
            p["rows"].append(entry)
            observations.append(entry)

    summaries = {}
    for name in STRATEGY_IDS:
        p = pool[name]
        normal = completed_trade_stats([
            {"Reason": "completed", "Return": r} for r in p["normal"]
        ])
        stress = completed_trade_stats([
            {"Reason": "completed", "Return": r} for r in p["stress"]
        ])
        summaries[name] = {
            "stocks": len(p["rows"]),
            "total_signals": sum(row["signals"] for row in p["rows"]),
            "pooled_completed_trades": normal["count"],
            "pooled_stress_completed_trades": stress["count"],
            "stocks_with_completed_trades": len(p["symbols"]),
            "pooled_completed_mean_net_return": normal["mean_net_return"],
            "pooled_stress_completed_mean_net_return": stress["mean_net_return"],
            "pooled_completed_profit_factor": normal["profit_factor"],
            "worst_single_stock_drawdown": max(
                (row["max_single_stock_drawdown"] for row in p["rows"]),
                default=None
            ),
            "median_completed_trades_per_stock": (
                median([row["completed_trades"] for row in p["rows"]])
                if p["rows"] else None
            ),
            "sum_of_independent_stock_test_charges_rs": sum(
                row["normal_charges_rs"] for row in p["rows"]
            ),
            "sum_of_independent_stock_stress_charges_rs": sum(
                row["stress_charges_rs"] for row in p["rows"]
            ),
        }

    implementation_sha256 = hashlib.sha256(b"".join(
        (CODE_ROOT / p).read_bytes() for p in CODE_PATHS
    )).hexdigest()
    ranking = rank_exploratory(summaries)
    return {
        "scope": "GROWW_DELIVERY_COSTS_EXPLORATORY_NOT_VALIDATED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "protocol_sha256": hashlib.sha256(
            json.dumps(PROTOCOL, sort_keys=True).encode()
        ).hexdigest(),
        "implementation_sha256": implementation_sha256,
        "data_sha256": digest,
        "eligible_universe": len(groups),
        "sample_symbols": symbols,
        "summary": summaries,
        "stock_observations": observations,
        "research_selection": ranking,
        "research_top_three_unvalidated": ranking["candidate_order"][:3],
        "verified_top_three": [],
        "verified_profitable_strategies": 0,
        "live_eligible": False,
        "validation_status": "NOT_VALIDATED",
        "orders": "DISABLED",
        "limitations": [
            "Historical evaluation was already inspected before these strategies were defined.",
            "Seven strategies compared; selecting the best creates multiple-testing bias.",
            "Per-stock 100000 INR simulation is NOT a multi-stock capital portfolio.",
            "Fees are estimates; historical broker tariffs may have differed, "
            "personal income taxes are unmodeled.",
            "Independent future periods, verified point-in-time fundamentals "
            "and forward paper fills required for any strategy approval.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be 1..100")
    root = data_root()
    frame, digest = read_history(root)
    result = compare(frame, digest, args.limit)
    path = root/"runtime"/"experiments"/VERSION_ID/"comparison.json"
    atomic_status(path, result)
    # Compact CLI: users can paste the actual P&L statistics, not 100 symbols.
    print(json.dumps({
        key: result[key] for key in (
            "scope", "eligible_universe", "summary",
            "research_top_three_unvalidated", "verified_top_three",
            "validation_status", "live_eligible",
        )
    }, indent=2))
    print("Detailed private report:", path)


if __name__ == "__main__":
    main()
