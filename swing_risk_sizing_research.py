"""Read-only capital-risk sizing experiment for three previously rejected swing ideas.

This is NOT a new profitable strategy. It retains frozen entry/exit signals,
draws from a previously inspected history window and merely changes exposure.
The 1% budget is hypothetical loss at initial stop including estimated fees;
an overnight gap or a broker execution problem can lose more than 1%.
Each stock has a separate fictional INR 100,000 ledger, not a portfolio.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from statistics import median

import pandas as pd

from research_monitor.groww_delivery_costs import simulate_delivery
from research_monitor.history import read_history
from research_monitor.paths import CODE_ROOT, data_root
from research_monitor.schedule import atomic_status
from swing_delivery_fee_research import BUILDERS, _signals_for
from swing_custom_strategy_research import END_DATE

VERSION = "fixed-one-percent-risk-v1"
RISK_FRACTION = .01
INITIAL_CAPITAL = 100000.
STRATEGIES = (
    "vcp-breakout-v1", "mean-reversion-v1", "custom-dip-reclaim-v1",
)
BUILDER_MAP = dict(BUILDERS)
CODE_FILES = (
    "swing_risk_sizing_research.py",
    "research_monitor/groww_delivery_costs.py",
    "swing_delivery_fee_research.py",
    "research_monitor/seven_strategy_rules.py",
    "swing_custom_strategy_research.py",
    "swing_backtest.py",
    "swing_pullback_research.py",
    "swing_strategy.py",
)
SCENARIOS = (
    ("all_cash_prior_model", dict(risk_fraction=None, slip=.001,
                                  stress_multiplier=1.0)),
    ("one_percent_initial_stop_budget", dict(risk_fraction=RISK_FRACTION,
                                             slip=.001, stress_multiplier=1.0)),
    ("one_percent_double_cost_stress", dict(risk_fraction=RISK_FRACTION,
                                            slip=.002, stress_multiplier=2.0)),
)
PROTOCOL = {
    "version": VERSION,
    "design_date": "2026-10-16",
    "historical_exploration_cutoff": END_DATE,
    "already_inspected": True,
    "sample_order": "SHA256(symbol) sorted ascending",
    "min_bars": 1260,
    "start_window": "220 warm-up bars + 70% of remaining observations",
    "per_stock_fictional_capital_rs": INITIAL_CAPITAL,
    "fixed_fraction_of_pre_trade_cash_at_risk": RISK_FRACTION,
    "budget_hypothesis": (
        "Loss if filled at the INITIAL stop after all estimated delivery "
        "charges and assumed exit slippage <= 1% of independent stock cash."
    ),
    "no_gap_loss_guarantee": True,
    "broker_orders": "DISABLED",
    "strategies": list(STRATEGIES),
    "scenarios": [name for name, _ in SCENARIOS],
}


def _summarize(reports, trades):
    completed = [t for t in trades if t["Reason"] != "sample_end"]
    capitals = [float(t["Return_On_Pre_Trade_Capital"]) for t in completed]
    entries = [float(t["Position_Entry_Fraction"]) for t in completed]
    risks = [t["Risk_Budget_Rs"] for t in completed
             if t.get("Risk_Budget_Rs") is not None]
    return {
        "stocks": len(reports),
        "completed_trades": len(completed),
        "completed_wins": sum(t["Return"] > 0 for t in completed),
        "completed_losses": sum(t["Return"] < 0 for t in completed),
        "stopped_completed_trades": sum(t["Reason"] == "stop" for t in completed),
        "realised_gap_stop_exits": sum(t.get("Gap_Below_Stop") is True
                                       for t in completed),
        "completed_mean_position_return_pct": (
            round(100*sum(t["Return"] for t in completed)/len(completed), 3)
            if completed else None
        ),
        "completed_mean_pre_trade_capital_return_pct": (
            round(100*sum(capitals)/len(capitals), 3)
            if capitals else None
        ),
        "median_fraction_of_cash_deployed_pct": (
            round(100*median(entries), 3) if entries else None
        ),
        "max_projected_no_gap_loss_over_budget_rs": (
            round(max(
                float(t["Projected_No_Gap_Stop_Loss_Rs"])-float(t["Risk_Budget_Rs"])
                for t in completed if t.get("Risk_Budget_Rs") is not None
            ), 6) if risks else None
        ),
        "completed_exits_exceeding_1pct_risk_budget": sum(
            t.get("Risk_Budget_Exceeded_On_Exit") is True for t in completed
        ),
        "sample_end_liquidations_excluded_from_completed_metrics": sum(
            int(r["sample_end_exits"]) for r in reports
        ),
        "median_independent_stock_marked_return_pct": (
            round(median([100*float(r["marked_return"]) for r in reports]), 3)
            if reports else None
        ),
        "worst_independent_stock_max_drawdown_pct": (
            round(100*max(float(r["drawdown"]) for r in reports), 3)
            if reports else None
        ),
        "fees_total_over_independent_stock_experiments_rs": round(
            sum(float(r["charges"]) for r in reports), 2
        ),
        "warning": (
            "No-gap 1% budget is not a maximum loss: actual gaps may breach it. "
            "Independent single-stock accounts cannot be summed as a portfolio."
        ),
    }


def compare(raw, digest, limit=30):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("LIMIT_1_TO_100")
    frozen = raw.loc[raw.Date <= pd.Timestamp(END_DATE)]
    groups = {sym: bars for sym,bars in frozen.groupby("Symbol")
              if len(bars) >= PROTOCOL["min_bars"]}
    symbols = sorted(groups, key=lambda s: hashlib.sha256(s.encode()).hexdigest())[:limit]
    buckets = {
        strategy: {name: {"reports": [], "trades": []}
                   for name,_ in SCENARIOS}
        for strategy in STRATEGIES
    }
    for sym in symbols:
        bars=groups[sym]
        cut=220+int((len(bars)-220)*.7)
        for strategy in STRATEGIES:
            d=_signals_for(strategy, BUILDER_MAP[strategy], bars)
            for name, options in SCENARIOS:
                status, ledger=simulate_delivery(
                    d, cut, len(d), capital=INITIAL_CAPITAL, **options
                )
                bucket=buckets[strategy][name]
                bucket["trades"].extend({"symbol":sym,**t} for t in ledger)
                bucket["reports"].append({
                    "symbol": sym,
                    "charges": status["Estimated_All_Trading_Charges_Rs"],
                    "drawdown": status["Max_Drawdown"],
                    "marked_return": (status["Final_Marked_Capital_Rs"]
                                      / INITIAL_CAPITAL-1),
                    "sample_end_exits": sum(t["Reason"]=="sample_end" for t in ledger),
                    "risk_budget_breaches": status["Realised_Risk_Budget_Breaches"],
                })
    summaries = {
        strategy: {
            scenario: _summarize(buckets[strategy][scenario]["reports"],
                                 buckets[strategy][scenario]["trades"])
            for scenario,_ in SCENARIOS
        }
        for strategy in STRATEGIES
    }
    code_sha=hashlib.sha256(b"".join(
        (CODE_ROOT/name).read_bytes() for name in CODE_FILES
    )).hexdigest()
    return {
        "scope": "EXPOSURE_RISK_AUDIT_ONLY_NOT_STRATEGY_VALIDATION",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "protocol_sha256": hashlib.sha256(
            json.dumps(PROTOCOL,sort_keys=True).encode()
        ).hexdigest(),
        "data_sha256": digest,
        "implementation_sha256": code_sha,
        "eligible_universe": len(groups),
        "sample_symbols": symbols,
        "summary": summaries,
        "top_three_profitable": [],
        "verified_profitable_strategies": 0,
        "strategy_trading_enabled": False,
        "live_eligible": False,
        "orders": "DISABLED",
        "limitations": [
            "No-gap loss is hypothetical and can be exceeded by actual exits.",
            "Original strategies already had negative expectancy in inspected history.",
            "Percent of each independent stock account, not a shared capital portfolio.",
            "Already inspected history cannot be used for untouched validation.",
            "No point-in-time fundamentals or forward paper-trade fills.",
        ],
    }


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=30)
    args=ap.parse_args()
    if not 1 <= args.limit <= 100:
        ap.error("--limit must be 1..100")
    root=data_root()
    raw,digest=read_history(root)
    result=compare(raw,digest,args.limit)
    path=root/"runtime"/"experiments"/VERSION/"comparison.json"
    atomic_status(path,result)
    print(json.dumps({
        "scope":result["scope"],
        "eligible_universe":result["eligible_universe"],
        "sample_size":len(result["sample_symbols"]),
        "summary":result["summary"],
        "verified_profitable_strategies":0,
        "live_eligible":False,
    },indent=2))
    print("Private 1%-risk research report:",path)


if __name__=="__main__":
    main()
