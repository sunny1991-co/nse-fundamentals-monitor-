"""Read-only ranking of fixed historical experiments, never trading approval.

Ranks already-frozen strategies on *completed* net trades; no parameter search.
Even a research leader requires new independent/forward evidence before use.
"""
from __future__ import annotations

import math

MIN_POOLED_COMPLETIONS = 30
MIN_DISTINCT_TRADED_STOCKS = 10
MIN_PROFIT_FACTOR = 1.5
MAX_WORST_SINGLE_STOCK_DRAWDOWN = 0.20


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def rank_exploratory(summaries):
    """Return reproducible research-only ranking; fail closed for missing data.

    Pooled trade statistics are observational, NOT portfolio performance:
    trades in different stocks overlap, have different liquidity and are not
    weighted by a realistic capital allocation. Holdout was already inspected.
    """
    rows = []
    for strategy in sorted(summaries):
        item = summaries[strategy]
        trades = _number(item.get("pooled_completed_trades"))
        stressed_trades = _number(item.get("pooled_stress_completed_trades"))
        stocks = _number(item.get("stocks_with_completed_trades"))
        mean = _number(item.get("pooled_completed_mean_net_return"))
        stressed_mean = _number(item.get("pooled_stress_completed_mean_net_return"))
        factor = _number(item.get("pooled_completed_profit_factor"))
        worst_drawdown = _number(item.get("worst_single_stock_drawdown"))
        reasons = []
        if trades is None or trades < MIN_POOLED_COMPLETIONS:
            reasons.append("INSUFFICIENT_COMPLETED_TRADES")
        if stressed_trades is None or stressed_trades < MIN_POOLED_COMPLETIONS:
            reasons.append("INSUFFICIENT_STRESS_TRADES")
        if stocks is None or stocks < MIN_DISTINCT_TRADED_STOCKS:
            reasons.append("TOO_FEW_DISTINCT_TRADED_STOCKS")
        if mean is None or mean <= 0:
            reasons.append("NET_COMPLETED_MEAN_NOT_POSITIVE")
        if stressed_mean is None or stressed_mean <= 0:
            reasons.append("DOUBLE_COST_NET_MEAN_NOT_POSITIVE")
        if factor is None or factor < MIN_PROFIT_FACTOR:
            reasons.append("COMPLETED_PROFIT_FACTOR_MISSING_OR_LT_1_5")
        if (worst_drawdown is None or worst_drawdown < 0
                or worst_drawdown > MAX_WORST_SINGLE_STOCK_DRAWDOWN):
            reasons.append("WORST_STOCK_DRAWDOWN_MISSING_OR_GT_20_PERCENT")
        rows.append({
            "strategy": strategy,
            "pooled_completed_trades": trades,
            "pooled_stress_completed_trades": stressed_trades,
            "stocks_with_completed_trades": stocks,
            "pooled_completed_mean_net_return": mean,
            "pooled_stress_completed_mean_net_return": stressed_mean,
            "pooled_completed_profit_factor": factor,
            "worst_single_stock_drawdown": worst_drawdown,
            "research_gate_pass": not reasons,
            "blockers": reasons,
        })

    # No best-looking return can circumvent the research evidence minimums.
    eligible = [r for r in rows if r["research_gate_pass"]]
    eligible.sort(key=lambda r: (
        -r["pooled_stress_completed_mean_net_return"],
        -r["pooled_completed_mean_net_return"],
        r["worst_single_stock_drawdown"],
        r["strategy"],
    ))
    leader = eligible[0]["strategy"] if eligible else None
    return {
        "status": ("EXPLORATORY_LEADER_REQUIRES_FRESH_VALIDATION" if leader
                   else "NO_RESEARCH_LEADER_WITH_SUFFICIENT_EVIDENCE"),
        "research_leader": leader,
        "candidate_order": [r["strategy"] for r in eligible],
        "observations": rows,
        "selection_basis": "POOLED_COMPLETED_NET_MEAN_UNDER_DOUBLE_COST_STRESS",
        "trade_execution": "DISABLED",
        "live_eligible": False,
        "verified_profitable_strategies": 0,
        "limitations": [
            "Previously inspected history cannot provide independent strategy validation.",
            "Pooled trade observations are not portfolio returns or future probabilities.",
            "Point-in-time fundamentals, realistic execution and prospective paper trading remain missing.",
        ],
    }
