"""Original swing hypothesis: controlled dip followed by trend resumption.

EXPLORATORY ONLY. It does not produce candidates, alerts or live orders.
The historical evaluation window is already inspected and cannot validate a
strategy invented now. Actual independent validation requires future data.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd

from research_monitor.exploratory_ranking import rank_exploratory
from research_monitor.history import read_history
from research_monitor.paths import CODE_ROOT, data_root
from research_monitor.schedule import atomic_status
from swing_backtest import COST, SLIP, VERSION, features, simulate
from swing_pullback_research import completed_trade_stats, pullback_features
from swing_strategy import ema

NAME = "custom-dip-reclaim-v1"
VERSION_ID = "custom-dip-reclaim-research-v1"
END_DATE = "2026-10-15"
RULES = (
    "EMA50 > EMA200; EMA200 > value 20 sessions ago; close > EMA50",
    "A low in previous 5 bars touched within 1% above prior EMA20",
    "At least one of three previous closes was below its EMA20",
    "Current close reclaims EMA20 and exceeds previous 3-session high",
    "Close in upper 30% of daily range, RSI14 in [50,72]",
    "Volume >= 20-session mean; 20-session mean traded value >= INR 5 crore",
    "ATR14 > 0 and <=6% of close; 0 < (close-stop)/close <=8%",
    "No same-session entry; use unchanged next-open backtest engine and exits",
)
PROTOCOL = {
    "version": VERSION_ID,
    "frozen_design_date": "2026-10-16",
    "exploration_end": END_DATE,
    "hypothesis": (
        "A controlled pullback to EMA20 followed by a strong closing reclaim "
        "within a rising long-term trend may avoid weak breakouts."
    ),
    "rules": RULES,
    "min_history_bars": 1260,
    "holdout_scope": "Previously inspected last 30% of post-warmup history; NOT independent",
    "symbol_order": "SHA256(symbol), not outcome-ranked",
    "same_sample_baselines": [VERSION, "trend-pullback-research-v1"],
    "normal_cost_per_side": COST,
    "normal_slippage_per_side": SLIP,
    "stress_multiplier": 2,
    "live_eligible": False,
}


def custom_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute an unoptimized, close-confirmed signal using only past/current bars."""
    d = features(bars)
    c, h, low, vol = d.Close, d.High, d.Low, d.Volume
    e20, e50, e200 = (ema(c, n) for n in (20, 50, 200))
    prior_close_below = (
        (c.shift(1) < e20.shift(1))
        | (c.shift(2) < e20.shift(2))
        | (c.shift(3) < e20.shift(3))
    )
    prior_dip = low.shift(1).rolling(5).min() <= 1.01 * e20.shift(1)
    breakout = c > h.shift(1).rolling(3).max()
    close_location = (c - low) / (h - low).replace(0, np.nan)
    stop_fraction = (c - d.Stop) / c
    d["Signal"] = (
        (e50 > e200) & (e200 > e200.shift(20)) & (c > e50)
        & prior_dip & prior_close_below
        & (c > e20) & breakout
        & (close_location >= .70) & d.RSI.between(50, 72)
        & (vol >= vol.rolling(20).mean())
        & ((c * vol).rolling(20).mean() >= 50_000_000)
        & (d.ATR > 0) & ((d.ATR / c) <= .06)
        & (d.Stop > 0) & (stop_fraction > 0) & (stop_fraction <= .08)
    ).fillna(False).astype(bool)
    return d


BUILDERS = (
    (VERSION, features),
    ("trend-pullback-research-v1", pullback_features),
    (NAME, custom_features),
)
CODE_PATHS = (
    "swing_custom_strategy_research.py",
    "swing_backtest.py",
    "swing_strategy.py",
    "swing_pullback_research.py",
    "research_monitor/exploratory_ranking.py",
)


def compare(raw: pd.DataFrame, digest: str, limit: int = 30) -> dict:
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("RESEARCH_LIMIT_MUST_BE_1_TO_100")
    frozen = raw.loc[raw.Date <= pd.Timestamp(END_DATE)]
    groups = {
        symbol: bars for symbol, bars in frozen.groupby("Symbol")
        if len(bars) >= PROTOCOL["min_history_bars"]
    }
    symbols = sorted(
        groups, key=lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest()
    )[:limit]
    pool = {
        name: {"base": [], "stress": [], "traded_symbols": set(), "rows": []}
        for name, _ in BUILDERS
    }
    observations = []
    for symbol in symbols:
        bars = groups[symbol]
        cut = 220 + int((len(bars)-220)*.7)
        for name, builder in BUILDERS:
            d = builder(bars)
            normal, normal_trades = simulate(d, cut, len(d))
            doubled, doubled_trades = simulate(
                d, cut, len(d), cost=COST * 2, slip=SLIP * 2
            )
            finished = completed_trade_stats(normal_trades)
            stressed = completed_trade_stats(doubled_trades)
            normal_returns = [
                float(t["Return"]) for t in normal_trades
                if t.get("Reason") not in (None, "sample_end")
            ]
            stress_returns = [
                float(t["Return"]) for t in doubled_trades
                if t.get("Reason") not in (None, "sample_end")
            ]
            p = pool[name]
            p["base"].extend(normal_returns)
            p["stress"].extend(stress_returns)
            if normal_returns:
                p["traded_symbols"].add(symbol)
            row = {
                "symbol": symbol,
                "strategy": name,
                "signals": int(d.Signal.iloc[cut-1:-1].sum()),
                "completed_trades": finished["count"],
                "stress_completed_trades": stressed["count"],
                "forced_sample_end_liquidations":
                    normal["Trades"] - normal["Completed_Trades"],
                "mean_completed_net_return": finished["mean_net_return"],
                "mean_stress_completed_net_return": stressed["mean_net_return"],
                "completed_profit_factor": finished["profit_factor"],
                "max_single_stock_drawdown": normal["Max_Drawdown"],
                "holdout_start": str(d.Date.iloc[cut].date()),
                "holdout_end": str(d.Date.iloc[-1].date()),
            }
            p["rows"].append(row)
            observations.append(row)

    summary = {}
    for name, _ in BUILDERS:
        p = pool[name]
        complete = completed_trade_stats([
            {"Reason": "completed", "Return": ret} for ret in p["base"]
        ])
        stress = completed_trade_stats([
            {"Reason": "completed", "Return": ret} for ret in p["stress"]
        ])
        summary[name] = {
            "stocks": len(p["rows"]),
            "signals": sum(r["signals"] for r in p["rows"]),
            "pooled_completed_trades": complete["count"],
            "pooled_stress_completed_trades": stress["count"],
            "stocks_with_completed_trades": len(p["traded_symbols"]),
            "pooled_completed_mean_net_return": complete["mean_net_return"],
            "pooled_stress_completed_mean_net_return": stress["mean_net_return"],
            "pooled_completed_profit_factor": complete["profit_factor"],
            "worst_single_stock_drawdown": max(
                (r["max_single_stock_drawdown"] for r in p["rows"]),
                default=None,
            ),
            "median_completed_trades_per_stock": (
                median([r["completed_trades"] for r in p["rows"]])
                if p["rows"] else None
            ),
        }

    implementation_sha256 = hashlib.sha256(b"".join(
        (CODE_ROOT / path).read_bytes() for path in CODE_PATHS
    )).hexdigest()
    return {
        "scope": "CUSTOM_STRATEGY_EXPLORATORY_NOT_VALIDATED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "protocol_sha256": hashlib.sha256(
            json.dumps(PROTOCOL, sort_keys=True).encode()
        ).hexdigest(),
        "implementation_sha256": implementation_sha256,
        "data_sha256": digest,
        "eligible_universe": len(groups),
        "sample_symbols": symbols,
        "summary": summary,
        "stock_observations": observations,
        "research_selection": rank_exploratory(summary),
        "verified_profitable_strategies": 0,
        "live_eligible": False,
        "validation_status": "NOT_VALIDATED",
        "orders": "DISABLED",
        "limitations": [
            "This is a new hypothesis designed after prior holdout was inspected.",
            "In-sample/inspected comparisons cannot establish out-of-sample profit.",
            "No point-in-time fundamentals, portfolio simulation or forward paper track record.",
            "All execution costs are illustrative; sample-end forced exits excluded "
            "from completed-trade means but appear separately.",
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
    report = compare(raw, digest, args.limit)
    output = root / "runtime" / "experiments" / VERSION_ID / "comparison.json"
    atomic_status(output, report)
    print(json.dumps({
        key: report[key] for key in (
            "scope", "eligible_universe", "summary",
            "research_selection", "validation_status", "live_eligible"
        )
    }, indent=2))
    print("Private research report:", output)


if __name__ == "__main__":
    main()
