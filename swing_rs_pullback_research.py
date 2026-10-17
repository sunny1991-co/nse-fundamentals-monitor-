"""Read-only three-way historical comparison for a NEW swing hypothesis.

No strategy auto-selection, live signal or broker order. Past 2026-10-16 was
already inspected in related experiments, hence even chronological final-30%
numbers are in-sample EXPLORATION and cannot validate future profitability.
Independent single-stock paper accounts, NOT a shared multi-stock portfolio.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json

import pandas as pd

from research_monitor.groww_delivery_costs import simulate_delivery
from research_monitor.history import read_history
from research_monitor.paths import CODE_ROOT, data_root
from research_monitor.rs_pullback_rules import (
    FROZEN_ON, PARAMETERS, STRATEGY_ID, rs_pullback_features,
)
from research_monitor.quality_leader_rules import quality_leader_features
from research_monitor.schedule import atomic_status
from research_monitor.strategy_sources import read_nifty50_tri
from swing_backtest import features

VERSION = "rs-pullback-research-v1"
FROZEN_SAMPLE_END = "2026-10-16"
BASELINE_BREAKOUT = "breakout-v1"
BASELINE_QUALITY = "quality-leader-contraction-breakout-v1"
SAMPLE_CAPITAL_RS = 100_000.0
CODE_PATHS = (
    "swing_rs_pullback_research.py",
    "research_monitor/rs_pullback_rules.py",
    "research_monitor/quality_leader_rules.py",
    "research_monitor/groww_delivery_costs.py",
    "research_monitor/history.py",
    "research_monitor/strategy_sources.py",
    "swing_backtest.py",
    "swing_strategy.py",
)
PROTOCOL = {
    "research_version": VERSION,
    "design_date": FROZEN_ON,
    "frozen_inspected_history_end": FROZEN_SAMPLE_END,
    "strategy": STRATEGY_ID,
    "frozen_rules": PARAMETERS,
    "comparison_strategies": [BASELINE_BREAKOUT, BASELINE_QUALITY],
    "stock_sampling": "SHA256_SYMBOL_NO_OUTCOME_SORTING",
    "same_stock_comparison": True,
    "minimum_history_sessions": 1260,
    "comparison_start": "LAST_30_PERCENT_AFTER_220_BAR_WARMUP_ALREADY_INSPECTED",
    "comparison_is_new_holdout": False,
    "point_in_time_fundamentals": False,
    "historical_point_in_time_universe_membership": False,
    "benchmark": "EXACT_DATE_NIFTY_50_TRI_NO_FILL_OPERATOR_ATTESTED",
    "execution": "NEXT_SESSION_OPEN_MAX_1_PERCENT_CHASE_STOP_FIRST",
    "normal_slippage_each_side": 0.001,
    "stress_slippage_each_side": 0.002,
    "stress_fee_multiplier": 2.0,
    "capital_per_independent_stock_account_rs": SAMPLE_CAPITAL_RS,
    "risk_fraction_at_model_stop": 0.01,
    "holding_sessions_max": 100,
    "portfolio_return_estimated": False,
    "orders": "DISABLED",
}


def finished(trades):
    done = [t for t in trades if t.get("Reason") != "sample_end"]
    values = [float(t["Return"]) for t in done]
    gain = sum(v for v in values if v > 0)
    loss = -sum(v for v in values if v < 0)
    return {
        "completed_trades": len(values),
        "wins": sum(v > 0 for v in values),
        "mean_net_position_return": sum(values)/len(values) if values else None,
        "net_profit_factor": gain/loss if loss > 0 else None,
        "stop_exits": sum(t.get("Reason") == "stop" for t in done),
        "gap_below_stop": sum(t.get("Gap_Below_Stop") is True for t in done),
        "risk_budget_breaches": sum(
            t.get("Risk_Budget_Exceeded_On_Exit") is True for t in done
        ),
        "censored_at_sample_end": len(trades)-len(done),
    }


def compare(raw, history_sha, nifty50_tri, tri_sha, *, limit=40,
            simulator=simulate_delivery):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("RESEARCH_LIMIT_1_TO_100")
    if not isinstance(raw, pd.DataFrame) or not {
        "Symbol", "Date", "Open", "High", "Low", "Close", "Volume"
    }.issubset(raw.columns):
        raise ValueError("ADJUSTED_PRICE_HISTORY_REQUIRED")
    if not isinstance(nifty50_tri, pd.Series):
        raise ValueError("OFFICIAL_NIFTY_50_TRI_REQUIRED")
    # Explicit exact-date gate even when simulated 0 trades; cannot infer a
    # regime from missing benchmark rows, spot NIFTY, or filled levels.
    from research_monitor.quality_leader_rules import _check_benchmark
    tri = _check_benchmark(nifty50_tri)
    if len(tri) < 260:
        raise ValueError("HISTORICAL_TRI_WARMUP_INSUFFICIENT")

    cutoff = pd.Timestamp(FROZEN_SAMPLE_END)
    frozen = raw.loc[pd.to_datetime(raw.Date) <= cutoff].copy()
    aligned_tri = tri.loc[tri.index <= cutoff]
    if len(aligned_tri) < 260:
        raise ValueError("FROZEN_TRI_WARMUP_INSUFFICIENT")
    groups = {
        name: d.sort_values("Date").reset_index(drop=True)
        for name, d in frozen.groupby("Symbol")
        if len(d) >= 1260
    }
    symbols = sorted(
        groups, key=lambda value: hashlib.sha256(value.encode()).hexdigest()
    )[:limit]
    strategies = (STRATEGY_ID, BASELINE_BREAKOUT, BASELINE_QUALITY)
    aggregated = {
        name: {
            "base": [], "stress": [], "signal_count": 0,
            "traded_symbols": set(), "max_drawdowns": [],
        } for name in strategies
    }
    observations = []
    for symbol in symbols:
        bars = groups[symbol]
        start = 220 + int((len(bars)-220)*0.70)
        built = {
            STRATEGY_ID: rs_pullback_features(bars, aligned_tri),
            BASELINE_BREAKOUT: features(bars),
            BASELINE_QUALITY: quality_leader_features(bars, aligned_tri),
        }
        for name, d in built.items():
            normal, trades = simulator(
                d, start, len(d), capital=SAMPLE_CAPITAL_RS, slip=.001,
                stress_multiplier=1.0, risk_fraction=.01, max_hold=100,
            )
            stressed, stressed_trades = simulator(
                d, start, len(d), capital=SAMPLE_CAPITAL_RS, slip=.002,
                stress_multiplier=2.0, risk_fraction=.01, max_hold=100,
            )
            summary, under_stress = finished(trades), finished(stressed_trades)
            record = aggregated[name]
            record["base"].extend(
                t for t in trades if t.get("Reason") != "sample_end"
            )
            record["stress"].extend(
                t for t in stressed_trades if t.get("Reason") != "sample_end"
            )
            if summary["completed_trades"]:
                record["traded_symbols"].add(symbol)
            record["signal_count"] += int(d.Signal.iloc[start-1:-1].sum())
            record["max_drawdowns"].append(float(normal["Max_Drawdown"]))
            observations.append({
                "symbol": symbol,
                "strategy": name,
                "window_start": str(pd.Timestamp(d.Date.iloc[start]).date()),
                "window_end": str(pd.Timestamp(d.Date.iloc[-1]).date()),
                "signals": int(d.Signal.iloc[start-1:-1].sum()),
                "completed_trades": summary["completed_trades"],
                "mean_net_return": summary["mean_net_position_return"],
                "stressed_mean_net_return": under_stress["mean_net_position_return"],
                "max_independent_stock_drawdown": float(normal["Max_Drawdown"]),
                "gap_below_stop_exits": summary["gap_below_stop"],
                "risk_budget_breaches": summary["risk_budget_breaches"],
            })
    comparison = {}
    for name, data in aggregated.items():
        base, stress = finished(data["base"]), finished(data["stress"])
        comparison[name] = {
            "sampled_stocks": len(symbols),
            "stocks_with_completed_trades": len(data["traded_symbols"]),
            "signals": data["signal_count"],
            "completed_trades": base["completed_trades"],
            "wins": base["wins"],
            "win_rate": (
                base["wins"]/base["completed_trades"]
                if base["completed_trades"] else None
            ),
            "mean_completed_net_position_return": base["mean_net_position_return"],
            "mean_double_cost_net_position_return": stress["mean_net_position_return"],
            "profit_factor": base["net_profit_factor"],
            "double_cost_profit_factor": stress["net_profit_factor"],
            "gap_below_stop_exits": base["gap_below_stop"],
            "risk_budget_breaches": base["risk_budget_breaches"],
            "worst_independent_stock_drawdown": max(
                data["max_drawdowns"], default=None
            ),
        }
    code_digest = hashlib.sha256(b"".join(
        (CODE_ROOT/p).read_bytes() for p in CODE_PATHS
    )).hexdigest()
    return {
        "scope": "FROZEN_RS_PULLBACK_SWING_HISTORICAL_EXPLORATORY_ONLY",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "protocol_sha256": hashlib.sha256(
            json.dumps(PROTOCOL, sort_keys=True).encode()
        ).hexdigest(),
        "implementation_sha256": code_digest,
        "historical_adjusted_price_sha256": history_sha,
        "nifty_tri_sha256": tri_sha,
        "eligible_historical_price_universe": len(groups),
        "sample_symbols": symbols,
        "comparison": comparison,
        "per_stock_research": observations,
        "no_new_independent_holdout": True,
        "forward_paper_track_record_verified": False,
        "historical_point_in_time_fundamentals_verified": False,
        "historical_universe_survivorship_bias_unresolved": True,
        "shared_portfolio_simulation": False,
        "strategy_profitability_validated": False,
        "calibrated_win_probability": None,
        "live_eligible": False,
        "trading_orders": "DISABLED",
        "limitations": [
            "All historical dates predate the design, so chronological splits are exploratory.",
            "Risk of strategy-mining and multiple-testing; no retrospective winner is promoted.",
            "Current NSE symbol universe is not an independently verified historical PIT universe.",
            "No historical audited consolidated company fundamentals used as historical eligibility.",
            "Per-stock separate accounts are not a single capital-constrained portfolio.",
            "Broker fee rates and slippage are modeled, not real execution.",
            "NIFTY 50 TRI source is operator-attested; missing exact TRI dates disable signals.",
            "Stop or overnight gap can exceed 1 percent modeled initial risk.",
            "No verified forward P&L, real-time feed, CNC protective order or order permissions.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    root = data_root()
    out = root/"runtime"/"experiments"/VERSION/"comparison.json"
    try:
        bars, digest = read_history(root)
        tri, tri_digest = read_nifty50_tri(root)
        result = compare(
            bars, digest, tri, tri_digest, limit=args.limit
        )
    except (FileNotFoundError, OSError, ValueError, TypeError):
        atomic_status(out, {
            "scope": "FROZEN_RS_PULLBACK_SWING_HISTORICAL_EXPLORATORY_ONLY",
            "state": "SOURCE_REQUIRED_OR_INVALID",
            "strategy_validated": False,
            "live_eligible": False,
            "trading_orders": "DISABLED",
        })
        print(json.dumps({
            "state": "SOURCE_REQUIRED_OR_INVALID",
            "live_eligible": False, "orders": "DISABLED"
        }, indent=2))
        return
    atomic_status(out, result)
    print(json.dumps({
        "scope": result["scope"],
        "eligible_historical_price_universe": result["eligible_historical_price_universe"],
        "sampled_stocks": len(result["sample_symbols"]),
        "comparison": result["comparison"],
        "strategy_profitability_validated": False,
        "live_eligible": False,
        "orders": "DISABLED",
    }, indent=2))
    print("Private retrospective report:", out)


if __name__ == "__main__":
    main()
