"""Seven fixed NSE equity strategies, exploratory backtest only.

Never promotes an active strategy, sends alerts, generates stock orders or
claims calibrated odds. Every strategy shares the unchanged next-open entry,
stop/target, fees and slippage model. Historical selection is NOT validation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median

import pandas as pd

from research_monitor.exploratory_ranking import rank_exploratory
from research_monitor.history import read_history
from research_monitor.paths import CODE_ROOT, data_root
from research_monitor.schedule import atomic_status
from research_monitor.seven_strategy_rules import (
    STRATEGIES, NEEDS_BENCHMARK, NEEDS_EARNINGS, signals,
)
from research_monitor.strategy_sources import (
    read_nifty50_tri, read_official_earnings,
)
from swing_backtest import features, simulate, COST, SLIP
from swing_pullback_research import completed_trade_stats

PROTOCOL = {
    "version": "seven-strategies-v1",
    "design_date": "2026-10-16",
    "historical_exploration_end": "2026-10-15",
    "minimum_bars_per_stock": 1260,
    "warmup_bars": 220,
    "comparison_window": "last 30 percent of post-warmup history, ALREADY INSPECTED",
    "sample_order": "SHA256(symbol), ascending; independent of returns",
    "strategy_ids": list(STRATEGIES),
    "benchmark_contract": "operator-attested NIFTY 50 TRI, exact-date alignment only",
    "earnings_contract": "dated original NSE-source consolidated results; next session after release",
    "execution": "shared breakout-v1 next-open, stop-first, targets, time exits",
    "illustrative_cost_per_side": COST,
    "illustrative_slippage_per_side": SLIP,
    "stress_multiplier": 2,
    "no_parameter_optimization": True,
    "no_live_order": True,
}

DEPENDENCIES = {
    NEEDS_BENCHMARK: "NIFTY_50_TRI_SOURCE_REQUIRED",
    NEEDS_EARNINGS: "DATED_OFFICIAL_EARNINGS_REQUIRED",
}


def source_inputs(root):
    """Optional sources fail closed; a missing one must not block other tests."""
    result = {"benchmark": None, "earnings": None,
              "statuses": {}, "source_hashes": {}}
    for kind, loader in (
        ("benchmark", read_nifty50_tri),
        ("earnings", read_official_earnings),
    ):
        try:
            value, digest = loader(root)
            result[kind] = value
            result["statuses"][kind] = "OPERATOR_ATTESTED_NOT_INDEPENDENTLY_VERIFIED"
            result["source_hashes"][kind] = digest
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError):
            result["statuses"][kind] = "MISSING_OR_UNVERIFIED_SOURCE"
    return result


def compare(raw, digest, limit=30, *, benchmark=None, earnings=None,
            source_statuses=None, source_hashes=None):
    if not 1 <= limit <= 100:
        raise ValueError("RESEARCH_SAMPLE_LIMIT_1_TO_100")
    frozen = raw[raw.Date <= pd.Timestamp(PROTOCOL["historical_exploration_end"])]
    groups = {sym: bars for sym, bars in frozen.groupby("Symbol")
              if len(bars) >= PROTOCOL["minimum_bars_per_stock"]}
    symbols = sorted(groups, key=lambda s: hashlib.sha256(s.encode()).hexdigest())[:limit]

    unavailable = {
        NEEDS_BENCHMARK: benchmark is None,
        NEEDS_EARNINGS: earnings is None,
    }
    statuses = {
        key: (DEPENDENCIES[key] if unavailable[key] else "EXPLORATORY_SOURCE_ATTESTED")
        for key in DEPENDENCIES
    }
    for name in STRATEGIES:
        if name not in statuses:
            statuses[name] = "EXPLORATORY_PRICE_HISTORY"
    pooled = {name: {"base": [], "stress": [], "symbols": set(), "rows": []}
              for name in STRATEGIES}
    observations = []
    for symbol in symbols:
        bars = groups[symbol]
        d = features(bars)
        cut = 220 + int((len(d) - 220) * .7)
        for strategy in STRATEGIES:
            if unavailable.get(strategy, False):
                continue
            current = d.copy(deep=False)
            current = current.assign(Signal=signals(
                d, strategy, benchmark=benchmark,
                announcement_dates=(earnings or {}).get(symbol),
            ))
            base, base_trades = simulate(current, cut, len(current))
            stress, stress_trades = simulate(
                current, cut, len(current), cost=COST*2, slip=SLIP*2,
            )
            base_stats = completed_trade_stats(base_trades)
            stress_stats = completed_trade_stats(stress_trades)
            base_returns = [
                float(t["Return"]) for t in base_trades
                if t.get("Reason") not in (None, "sample_end")
            ]
            stress_returns = [
                float(t["Return"]) for t in stress_trades
                if t.get("Reason") not in (None, "sample_end")
            ]
            pool = pooled[strategy]
            pool["base"].extend(base_returns)
            pool["stress"].extend(stress_returns)
            if base_returns:
                pool["symbols"].add(symbol)
            row = {
                "symbol": symbol, "strategy": strategy,
                "holdout_start": str(d.Date.iloc[cut].date()),
                "holdout_end": str(d.Date.iloc[-1].date()),
                "signals": int(current.Signal.iloc[cut-1:len(d)-1].sum()),
                "completed_trades": base_stats["count"],
                "forced_sample_end_liquidations": base["Trades"] - base["Completed_Trades"],
                "completed_mean_net_return": base_stats["mean_net_return"],
                "stress_completed_mean_net_return": stress_stats["mean_net_return"],
                "completed_profit_factor": base_stats["profit_factor"],
                "worst_drawdown": base["Max_Drawdown"],
            }
            pool["rows"].append(row)
            observations.append(row)

    summaries = {}
    for name in STRATEGIES:
        if unavailable.get(name, False):
            continue
        pool = pooled[name]
        base = completed_trade_stats([
            {"Reason": "completed", "Return": r} for r in pool["base"]
        ])
        stress = completed_trade_stats([
            {"Reason": "completed", "Return": r} for r in pool["stress"]
        ])
        summaries[name] = {
            "stocks": len(pool["rows"]),
            "pooled_completed_trades": base["count"],
            "pooled_stress_completed_trades": stress["count"],
            "stocks_with_completed_trades": len(pool["symbols"]),
            "pooled_completed_mean_net_return": base["mean_net_return"],
            "pooled_stress_completed_mean_net_return": stress["mean_net_return"],
            "pooled_completed_profit_factor": base["profit_factor"],
            "worst_single_stock_drawdown": max(
                (r["worst_drawdown"] for r in pool["rows"]), default=None,
            ),
            "stocks_without_completed_trades": sum(
                r["completed_trades"] == 0 for r in pool["rows"]
            ),
            "median_completed_trades_per_stock": (
                median([r["completed_trades"] for r in pool["rows"]])
                if pool["rows"] else None
            ),
            "signals": sum(r["signals"] for r in pool["rows"]),
        }

    ranking = rank_exploratory(summaries)
    top_three = ranking["candidate_order"][:3]
    # These are research candidates only. An arbitrary three cannot be
    # activated when evidence requirements fail, or if fewer than three pass.
    code_paths = (
        "swing_seven_strategy_research.py",
        "research_monitor/seven_strategy_rules.py",
        "research_monitor/strategy_sources.py",
        "research_monitor/exploratory_ranking.py",
        "swing_backtest.py",
        "swing_strategy.py",
        "swing_pullback_research.py",
    )
    implementation_hash = hashlib.sha256(
        b"".join((CODE_ROOT / p).read_bytes() for p in code_paths)
    ).hexdigest()
    return {
        "scope": "SEVEN_HYPOTHESES_EXPLORATORY_ONLY",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_sha256": digest,
        "implementation_sha256": implementation_hash,
        "protocol_sha256": hashlib.sha256(
            json.dumps(PROTOCOL, sort_keys=True).encode()
        ).hexdigest(),
        "protocol": PROTOCOL,
        "source_statuses": source_statuses or {},
        "source_hashes": source_hashes or {},
        "eligible_universe": len(groups),
        "sample_symbols": symbols,
        "strategy_statuses": statuses,
        "summary": summaries,
        "stock_observations": observations,
        "research_selection": ranking,
        "research_top_three_unvalidated": top_three,
        "verified_top_three": [],
        "verified_profitable_strategies": 0,
        "validation_status": "NOT_VALIDATED",
        "live_eligible": False,
        "orders": "DISABLED",
        "limitations": [
            "Comparison uses already-inspected history, not independent holdout.",
            "Strategy search itself creates multiple-testing/overfitting risk.",
            "Overlapping individual-stock trades are not portfolio returns.",
            "Earnings and benchmark files, if present, are operator-attested only.",
            "Past universe membership and fundamentals are not point-in-time verified.",
            "True taxes, spreads, liquidity, and forward paper fills unverified.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be 1..100")
    root = data_root()
    raw, digest = read_history(root)
    optional = source_inputs(root)
    report = compare(
        raw, digest, args.limit,
        benchmark=optional["benchmark"],
        earnings=optional["earnings"],
        source_statuses=optional["statuses"],
        source_hashes=optional["source_hashes"],
    )
    path = root / "runtime" / "experiments" / PROTOCOL["version"] / "comparison.json"
    atomic_status(path, report)
    summary = {
        key: report[key] for key in (
            "scope", "eligible_universe", "strategy_statuses", "summary",
            "research_top_three_unvalidated", "verified_top_three",
            "validation_status", "live_eligible",
        )
    }
    print(json.dumps(summary, indent=2))
    print("Private research report:", path)


if __name__ == "__main__":
    main()
