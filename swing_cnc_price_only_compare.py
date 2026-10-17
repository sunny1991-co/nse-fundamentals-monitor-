"""Matched read-only NSE EQ swing backtest WITHOUT NIFTY TRI dependency.

Existing, already-frozen price-only hypotheses:
  breakout-v1, trend-pullback-research-v1, custom-dip-reclaim-v1.

Research ONLY, historical outcomes already inspected before this report
design. Uses adjusted, hash-attested NSE OHLCV; no fake index, no point-in-time
company fundamentals, no live orders, no automatic strategy promotion.
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
from research_monitor.schedule import atomic_status
from swing_backtest import features
from swing_pullback_research import pullback_features
from swing_custom_strategy_research import custom_features

VERSION = "cnc-price-only-delivery-comparison-v1"
END_DATE = "2026-10-15"
CAPITAL = 100_000.0
STRATEGIES = (
    ("breakout-v1", features),
    ("trend-pullback-research-v1", pullback_features),
    ("custom-dip-reclaim-v1", custom_features),
)
SOURCE_FILES = (
    "swing_cnc_price_only_compare.py",
    "swing_backtest.py",
    "swing_strategy.py",
    "swing_pullback_research.py",
    "swing_custom_strategy_research.py",
    "research_monitor/groww_delivery_costs.py",
    "research_monitor/history.py",
)
PROTOCOL = {
    "version": VERSION,
    "designed_on": "2026-10-17",
    "retrospective_end": END_DATE,
    "sample_is_independent_out_of_sample": False,
    "strategies_existing_frozen_price_only": [n for n, _ in STRATEGIES],
    "no_tri_or_price_nifty_substitute": True,
    "no_historical_point_in_time_fundamentals": True,
    "no_historical_survivorship_free_universe": True,
    "adjusted_ohlcv_and_source_sha_required": True,
    "minimum_stock_bars": 1260,
    "stock_sampling": "SHA256_SYMBOL_FIXED_ORDER",
    "comparison_window": "LAST_30_PERCENT_AFTER_220_BAR_WARMUP_ALREADY_INSPECTED",
    "position_engine": "NEXT_OPEN_NO_GAP_CHASE_STOP_FIRST_2R_3R_TRAIL",
    "broker_charge_model": "ILLUSTRATIVE_GROWW_NSE_CNC_DELIVERY_WITH_DP_AND_TAXES",
    "capital_per_separate_stock_account_rupees": CAPITAL,
    "modeled_initial_stop_risk_fraction": 0.01,
    "normal_slippage_each_side": 0.001,
    "stress_slippage_each_side": 0.002,
    "stress_fees_multiplier": 2.0,
    "max_holding_sessions": 100,
    "shared_portfolio_backtest": False,
    "live_trading": False,
}


def completed(trades):
    finished = [t for t in trades if t.get("Reason") not in (None, "sample_end")]
    returns = [float(t["Return"]) for t in finished]
    gains = sum(x for x in returns if x > 0)
    losses = -sum(x for x in returns if x < 0)
    return {
        "count": len(returns),
        "wins": sum(x > 0 for x in returns),
        "mean_completed_net_position_return": (
            sum(returns) / len(returns) if returns else None
        ),
        "profit_factor": gains / losses if losses > 0 else None,
        "overnight_gap_below_stop_exits": sum(
            t.get("Gap_Below_Stop") is True for t in finished
        ),
        "modeled_stop_risk_budget_breaches": sum(
            t.get("Risk_Budget_Exceeded_On_Exit") is True for t in finished
        ),
        "sample_end_censored_trades": len(trades) - len(finished),
    }


def compare(history: pd.DataFrame, history_digest: str, *,
            limit: int = 12, simulator=simulate_delivery,
            builders=STRATEGIES):
    if isinstance(limit, bool) or type(limit) is not int or not 1 <= limit <= 60:
        raise ValueError("PRICE_ONLY_LIMIT_1_TO_60")
    needed = {"Symbol", "Date", "Open", "High", "Low", "Close", "Volume"}
    if not isinstance(history, pd.DataFrame) or not needed.issubset(history):
        raise ValueError("ADJUSTED_NSE_OHLCV_REQUIRED")
    if not isinstance(history_digest, str) or not history_digest:
        raise ValueError("SOURCE_HASH_ATTESTATION_REQUIRED")
    if {name for name, _ in builders} != {name for name, _ in STRATEGIES}:
        raise ValueError("FROZEN_STRATEGY_IDS_REQUIRED")

    frozen = history.loc[pd.to_datetime(history.Date) <= pd.Timestamp(END_DATE)]
    groups = {
        symbol: group.sort_values("Date").reset_index(drop=True)
        for symbol, group in frozen.groupby("Symbol")
        if len(group) >= 1260
    }
    eligible_symbols = sorted(
        groups, key=lambda x: hashlib.sha256(x.encode("utf-8")).hexdigest()
    )
    sample = eligible_symbols[:limit]
    if not sample:
        return {
            "scope": "PRICE_ONLY_DELIVERY_RETROSPECTIVE_RESEARCH_NO_TRI",
            "state": "NO_STOCK_WITH_1260_ADJUSTED_BARS_BEFORE_CUTOFF",
            "eligible_stocks": 0,
            "sampled_stocks": 0,
            "strategy_profitability_validated": False,
            "live_eligible": False,
            "orders": "DISABLED",
        }

    bucket = {
        name: {
            "ordinary": [], "stress": [], "symbols": set(),
            "signals": 0, "worst_drawdowns": [], "censored": 0,
        }
        for name, _ in builders
    }
    details = []
    for symbol in sample:
        bars = groups[symbol]
        start = 220 + int((len(bars) - 220) * .7)
        for name, builder in builders:
            signals_frame = builder(bars.copy())
            # Input prices filtered before signal computation; no future
            # post-design rows can leak into even the rolling indicators.
            usual, trades = simulator(
                signals_frame, start, len(signals_frame),
                capital=CAPITAL, slip=.001, stress_multiplier=1.,
                risk_fraction=.01, max_hold=100,
            )
            stressed, stressed_trades = simulator(
                signals_frame, start, len(signals_frame),
                capital=CAPITAL, slip=.002, stress_multiplier=2.,
                risk_fraction=.01, max_hold=100,
            )
            basic = completed(trades)
            under_stress = completed(stressed_trades)
            record = bucket[name]
            record["ordinary"].extend(
                t for t in trades if t.get("Reason") not in (None, "sample_end")
            )
            record["stress"].extend(
                t for t in stressed_trades
                if t.get("Reason") not in (None, "sample_end")
            )
            record["signals"] += int(signals_frame.Signal.iloc[start-1:-1].sum())
            record["worst_drawdowns"].append(float(usual["Max_Drawdown"]))
            record["censored"] += basic["sample_end_censored_trades"]
            if basic["count"]:
                record["symbols"].add(symbol)
            details.append({
                "symbol": symbol, "strategy": name,
                "window_start": str(pd.Timestamp(signals_frame.Date.iloc[start]).date()),
                "window_end": str(pd.Timestamp(signals_frame.Date.iloc[-1]).date()),
                "signals": int(signals_frame.Signal.iloc[start-1:-1].sum()),
                "completed_trades": basic["count"],
                "mean_completed_net_return": basic["mean_completed_net_position_return"],
                "mean_stress_net_return": under_stress[
                    "mean_completed_net_position_return"
                ],
                "max_independent_stock_drawdown": float(usual["Max_Drawdown"]),
                "gap_below_stop_exits": basic["overnight_gap_below_stop_exits"],
                "risk_budget_breaches": basic["modeled_stop_risk_budget_breaches"],
                "censored_at_sample_end": basic["sample_end_censored_trades"],
            })

    scores = {}
    for name, stats in bucket.items():
        normal = completed(stats["ordinary"])
        stress = completed(stats["stress"])
        scores[name] = {
            "sampled_stocks": len(sample),
            "stocks_with_completed_trades": len(stats["symbols"]),
            "signals": stats["signals"],
            "completed_trades": normal["count"],
            "wins": normal["wins"],
            "net_win_rate": normal["wins"]/normal["count"] if normal["count"] else None,
            "mean_completed_net_position_return": normal[
                "mean_completed_net_position_return"
            ],
            "mean_double_cost_net_position_return": stress[
                "mean_completed_net_position_return"
            ],
            "completed_profit_factor": normal["profit_factor"],
            "double_cost_profit_factor": stress["profit_factor"],
            "overnight_gap_below_stop_exits": normal["overnight_gap_below_stop_exits"],
            "modeled_stop_risk_budget_breaches": normal[
                "modeled_stop_risk_budget_breaches"
            ],
            "censored_sample_end_trades": stats["censored"],
            "worst_independent_stock_drawdown": max(
                stats["worst_drawdowns"], default=None
            ),
        }

    return {
        "scope": "PRICE_ONLY_DELIVERY_RETROSPECTIVE_RESEARCH_NO_TRI",
        "state": "EXPLORATORY_COMPARISON_COMPLETE_NOT_VALIDATED",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "protocol_sha256": hashlib.sha256(
            json.dumps(PROTOCOL, sort_keys=True).encode()
        ).hexdigest(),
        "implementation_sha256": hashlib.sha256(b"".join(
            (CODE_ROOT/p).read_bytes() for p in SOURCE_FILES
        )).hexdigest(),
        "adjusted_nse_history_sha256": history_digest,
        "eligible_historical_stocks": len(groups),
        "sampled_stocks": len(sample),
        "sample_symbols": sample,
        "summary": scores,
        "per_stock_observations": details,
        "benchmark_based_strategy_tested": False,
        "tri_source_available_for_this_run": False,
        "historical_point_in_time_fundamentals_verified": False,
        "independent_out_of_sample_pass": False,
        "prospective_paper_profitability_verified": False,
        "shared_portfolio_returns_measured": False,
        "strategy_profitability_validated": False,
        "verified_profitable_strategies": 0,
        "calibrated_win_probability": None,
        "live_eligible": False,
        "orders": "DISABLED",
        "limitations": [
            "Already-inspected historical sample, NOT a new holdout or forward test.",
            "Only stock-price/volume technical features; NO verified NIFTY TRI comparison.",
            "No historical point-in-time annual NSE consolidated fundamentals or membership.",
            "Independent per-stock accounts are not a common capital-constrained portfolio.",
            "Estimated Groww delivery costs, DP and slippage may differ from actual fills.",
            "A gap below stop can lose more than modeled 1 percent risk.",
            "3 strategies were chosen before this comparison; no best strategy automatically selected.",
            "Strict live trading and BUY approvals remain disabled.",
        ],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=12)
    args = p.parse_args()
    root = data_root()
    path = root/"runtime"/"experiments"/VERSION/"comparison.json"
    try:
        bars, digest = read_history(root)
        result = compare(bars, digest, limit=args.limit)
    except (FileNotFoundError, OSError, ValueError, TypeError, KeyError):
        result = {
            "scope": "PRICE_ONLY_DELIVERY_RETROSPECTIVE_RESEARCH_NO_TRI",
            "state": "ADJUSTED_STOCK_HISTORY_MISSING_OR_INVALID",
            "strategy_profitability_validated": False,
            "live_eligible": False,
            "orders": "DISABLED",
        }
    atomic_status(path, result)
    print(json.dumps({
        key: result.get(key) for key in (
            "scope", "state", "eligible_historical_stocks", "sampled_stocks",
            "summary", "independent_out_of_sample_pass",
            "strategy_profitability_validated", "live_eligible", "orders",
        )
    }, indent=2))
    print("Private read-only historical report:", path)


if __name__ == "__main__":
    main()
