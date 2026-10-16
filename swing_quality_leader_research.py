"""Frozen, read-only swing strategy *historical hypothesis* comparison.

Dates through 2026-10-15 were inspected before this ruleset was invented.
NO portion of this history is a genuinely independent holdout, even if
the engine labels a chronological final 30% as a 'comparison window'.
Use external attested NIFTY 50 TRI levels (not spot NIFTY) and adjusted
NSE OHLCV. Missing sources STOP this strategy; they are never fabricated.

Execute only in local research mode; user has NOT approved broker orders,
SMS buy signals or real-money position execution.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

import pandas as pd

from research_monitor.groww_delivery_costs import simulate_delivery
from research_monitor.history import read_history
from research_monitor.paths import CODE_ROOT,data_root
from research_monitor.quality_leader_rules import (
    FROZEN_RULE_DATE, PARAMETERS,STRATEGY_ID,quality_leader_features,
)
from research_monitor.schedule import atomic_status
from research_monitor.strategy_sources import read_nifty50_tri
from swing_backtest import features
from swing_custom_strategy_research import END_DATE

VERSION="quality-leader-backtest-research-v1"
COMPARATOR="breakout-v1"
SOURCE_CODE_FILES=(
    "swing_quality_leader_research.py",
    "research_monitor/quality_leader_rules.py",
    "research_monitor/groww_delivery_costs.py",
    "swing_backtest.py",
    "swing_strategy.py",
    "research_monitor/history.py",
    "research_monitor/strategy_sources.py",
)
CAPITAL=100_000.0
RISK_FRACTION=0.01
FROZEN_SAMPLE_END="2026-10-15"
PROTOCOL={
    "version":VERSION,
    "defined":FROZEN_RULE_DATE,
    "frozen_retrospective_end":FROZEN_SAMPLE_END,
    "strategy":STRATEGY_ID,
    "historical_interpretation":"ALREADY_INSPECTED_RETROSPECTIVE_NOT_A_GENUINE_HOLDOUT",
    "parameters":PARAMETERS,
    "nifty_benchmark":"OFFICIAL_ATTESTED_NIFTY_50_TRI_EXACT_DATED_NO_FILL",
    "relative_strength":"STOCK_63_SESSION_RETURN_MINUS_TRI_63_SESSION_RETURN",
    "portfolio":"INDEPENDENT_SINGLE_STOCK_SUBACCOUNTS_NOT_SHARED_CAPITAL",
    "capital_per_independent_stock_account_rs":CAPITAL,
    "position_initial_stop_budget_fraction":RISK_FRACTION,
    "normal_slippage_per_side":.001,
    "stress_slippage_per_side":.002,
    "stress_delivery_fee_multiplier":2.,
    "entry":"PREVIOUS_DAILY_CLOSE_SIGNAL_NEXT_OPEN_NO_GAP_CHASE",
    "stop_order":"DAILY_OHLC_STOP_FIRST_AND_OPEN_GAP_RISK",
    "max_holding_sessions":100,
    "live_trading":False,
}


def _finished(trades):
    completed=[
        t for t in trades if t.get("Reason")!="sample_end"
    ]
    values=[float(t["Return"]) for t in completed]
    profits=sum(v for v in values if v>0)
    losses=-sum(v for v in values if v<0)
    return {
        "completed_trades":len(completed),
        "wins":sum(v>0 for v in values),
        "mean_completed_net_position_return":(
            sum(values)/len(values) if values else None
        ),
        "net_profit_factor":(profits/losses if losses>0 else None),
        "stop_exits":sum(t["Reason"]=="stop" for t in completed),
        "gap_below_stop":sum(t.get("Gap_Below_Stop") is True
                             for t in completed),
        "initial_stop_budget_breaches":sum(
            t.get("Risk_Budget_Exceeded_On_Exit") is True
            for t in completed
        ),
        "forced_sample_end_count":len(trades)-len(completed),
    }


def compare(raw, historical_sha, levels, benchmark_sha, limit=40,
            simulator=simulate_delivery):
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=100:
        raise ValueError("RESEARCH_SAMPLE_LIMIT_1_TO_100")
    if not isinstance(raw,pd.DataFrame) or not {
        "Symbol","Date","Open","High","Low","Close","Volume"
    }.issubset(raw.columns):
        raise ValueError("ADJUSTED_PRICE_HISTORY_REQUIRED")
    if not isinstance(levels,pd.Series):
        raise ValueError("NIFTY_50_TRI_SERIES_REQUIRED")
    cutoff=pd.Timestamp(FROZEN_SAMPLE_END)
    frozen=raw.loc[pd.to_datetime(raw.Date)<=cutoff]
    tri=levels.loc[levels.index<=cutoff]
    if len(tri)<260:
        raise ValueError("OFFICIAL_HISTORICAL_TRI_WARMUP_INSUFFICIENT")
    groups={
        symbol:d.sort_values("Date").reset_index(drop=True)
        for symbol,d in frozen.groupby("Symbol")
        if len(d)>=1260
    }
    symbols=sorted(
        groups,key=lambda value:hashlib.sha256(value.encode()).hexdigest()
    )[:limit]
    stored={}
    observations=[]
    for name in (STRATEGY_ID,COMPARATOR):
        stored[name]={
            "base":[],"stressed":[],"traded_symbols":set(),
            "risk_breaches":0,"gap_stops":0,"sample_end":0,
            "signals":0,"stock_drawdowns":[],
        }
    for symbol in symbols:
        bars=groups[symbol]
        start=220+int((len(bars)-220)*.7)
        strategy=quality_leader_features(bars,tri)
        original=features(bars)
        for name,d in ((STRATEGY_ID,strategy),(COMPARATOR,original)):
            normal,trades=simulator(
                d,start,len(d),capital=CAPITAL,slip=.001,
                stress_multiplier=1.,risk_fraction=RISK_FRACTION,
                max_hold=100,
            )
            stressed,stressed_trades=simulator(
                d,start,len(d),capital=CAPITAL,slip=.002,
                stress_multiplier=2.,risk_fraction=RISK_FRACTION,
                max_hold=100,
            )
            completed=_finished(trades)
            stress_metrics=_finished(stressed_trades)
            target=stored[name]
            target["base"].extend(
                t for t in trades if t.get("Reason")!="sample_end"
            )
            target["stressed"].extend(
                t for t in stressed_trades if t.get("Reason")!="sample_end"
            )
            if completed["completed_trades"]:
                target["traded_symbols"].add(symbol)
            target["signals"]+=int(d.Signal.iloc[start-1:-1].sum())
            target["risk_breaches"]+=completed[
                "initial_stop_budget_breaches"
            ]
            target["gap_stops"]+=completed["gap_below_stop"]
            target["sample_end"]+=completed["forced_sample_end_count"]
            target["stock_drawdowns"].append(float(normal["Max_Drawdown"]))
            observations.append({
                "symbol":symbol,
                "strategy":name,
                "retrospective_window_start":str(
                    pd.Timestamp(d.Date.iloc[start]).date()
                ),
                "retrospective_window_end":str(
                    pd.Timestamp(d.Date.iloc[-1]).date()
                ),
                "signals":int(d.Signal.iloc[start-1:-1].sum()),
                "completed_trades":completed["completed_trades"],
                "stress_completed_trades":stress_metrics["completed_trades"],
                "mean_completed_net_position_return":
                    completed["mean_completed_net_position_return"],
                "stressed_mean_completed_net_position_return":
                    stress_metrics["mean_completed_net_position_return"],
                "max_stock_drawdown":float(normal["Max_Drawdown"]),
                "gapped_stops":completed["gap_below_stop"],
                "risk_budget_breaches":completed[
                    "initial_stop_budget_breaches"
                ],
            })
    summary={}
    for name,stats in stored.items():
        plain=_finished(stats["base"])
        stress=_finished(stats["stressed"])
        summary[name]={
            "sampled_stocks":len(symbols),
            "signals":stats["signals"],
            "completed_trades":plain["completed_trades"],
            "completed_trades_double_cost":stress["completed_trades"],
            "stocks_with_completed_trades":len(stats["traded_symbols"]),
            "mean_completed_net_position_return":
                plain["mean_completed_net_position_return"],
            "mean_double_cost_net_position_return":
                stress["mean_completed_net_position_return"],
            "profit_factor":plain["net_profit_factor"],
            "stressed_profit_factor":stress["net_profit_factor"],
            "gap_below_stop_exits":stats["gap_stops"],
            "risk_budget_breaches":stats["risk_breaches"],
            "forced_sample_end_exits_excluded_from_completed_mean":
                stats["sample_end"],
            "worst_independent_stock_drawdown":max(
                stats["stock_drawdowns"],default=None
            ),
        }
    digest=hashlib.sha256(b"".join(
        (CODE_ROOT/path).read_bytes() for path in SOURCE_CODE_FILES
    )).hexdigest()
    return {
        "scope":"FROZEN_QUALITY_LEADER_SWING_EXPLORATORY_ONLY",
        "created_at_utc":datetime.now(timezone.utc).isoformat(),
        "protocol":PROTOCOL,
        "protocol_sha256":hashlib.sha256(
            json.dumps(PROTOCOL,sort_keys=True).encode()
        ).hexdigest(),
        "implementation_sha256":digest,
        "adjusted_eod_history_sha256":historical_sha,
        "benchmark_tri_sha256":benchmark_sha,
        "historical_eligible_universe":len(groups),
        "sampled_symbols":symbols,
        "summary":summary,
        "per_stock_observations":observations,
        "independent_holdout_pass":False,
        "forward_paper_track_record_available":False,
        "historical_point_in_time_fundamentals_verified":False,
        "historical_universe_survivorship_bias_unresolved":True,
        "shared_portfolio_performance_not_measured":True,
        "strategy_validated_profitable":False,
        "verified_profitable_strategies_added":0,
        "calibrated_win_probability":None,
        "live_eligible":False,
        "groww_orders":"DISABLED",
        "limitations":[
            "Historical source and past strategy returns were already inspected "
            "before this hypothesis; split results are not fresh evidence.",
            "No point-in-time company constituents or 4-year NSE audited "
            "consolidated fundamentals used for historical eligibility.",
            "Independent stock accounts are not a shared-capital multi-stock portfolio.",
            "Past TRI source metadata is operator attestation, not independent audit.",
            "Gap losses can exceed modeled 1% initial position risk; "
            "fees and slippage are scenario assumptions.",
            "No real-time feed, independent prospective paper track record, "
            "forecast calibration or broker execution permission.",
        ],
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit",type=int,default=40)
    args=parser.parse_args()
    root=data_root()
    output=root/"runtime"/"experiments"/VERSION/"comparison.json"
    try:
        raw,digest=read_history(root)
        benchmark,benchmark_digest=read_nifty50_tri(root)
        result=compare(
            raw,digest,benchmark,benchmark_digest,limit=args.limit
        )
    except (FileNotFoundError,OSError,ValueError,TypeError) as exc:
        reason=(
            "SOURCE_REQUIRED_OR_INVALID_NIFTY_50_TRI_OR_ADJUSTED_PRICE_HISTORY"
            if isinstance(exc,(FileNotFoundError,OSError)) else
            "FROZEN_RESEARCH_INPUT_INVALID"
        )
        atomic_status(output,{
            "scope":"FROZEN_QUALITY_LEADER_SWING_EXPLORATORY_ONLY",
            "state":"BLOCKED_BY_MISSING_OR_INVALID_VERIFIED_INPUT",
            "reason_code":reason,
            "validated":False,
            "live_eligible":False,
            "groww_orders":"DISABLED",
        })
        print(json.dumps({
            "state":"BLOCKED_BY_MISSING_OR_INVALID_VERIFIED_INPUT",
            "reason_code":reason,
            "groww_orders":"DISABLED"
        },indent=2))
        return
    atomic_status(output,result)
    print(json.dumps({
        "scope":result["scope"],
        "historical_eligible_universe":result["historical_eligible_universe"],
        "sample_size":len(result["sampled_symbols"]),
        "summary":result["summary"],
        "independent_holdout_pass":False,
        "live_eligible":False,
        "groww_orders":"DISABLED",
    },indent=2))
    print("Private exploratory comparison:",output)


if __name__=="__main__":
    main()
