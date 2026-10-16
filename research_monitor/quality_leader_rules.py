"""Frozen *research-only* quality-leader momentum swing entry, no orders.

Strategy hypothesis, defined on 2026-10-16 AFTER the older NSE history
had already been inspected. Therefore ALL historical tests are exploratory.
An independent forward track record is required before any profitability
or calibration claim. "Quality" refers to price/volume quality only:
historical fundamentals are NOT silently applied backward.

Signals use close-of-session completed OHLCV and exactly dated, attested
NIFTY 50 TRI levels. A signal may be executed only at NEXT session's open.
Missing benchmark dates do NOT forward-fill, fallback to price NIFTY, or
activate signals. Single-stock stop/target execution is handled by existing
Groww delivery simulator; no broker token is used.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from swing_backtest import features
from swing_strategy import ema

STRATEGY_ID = "quality-leader-contraction-breakout-v1"
FROZEN_RULE_DATE = "2026-10-16"
PARAMETERS = {
    "min_bars": 260,
    "stock_return_63_min": 0.10,
    "relative_to_tri_return_63_min": 0.05,
    "breakout_prior_sessions": 30,
    "benchmark_slope_sessions": 20,
    "stock_ema200_slope_sessions": 20,
    "recent_volatility_sessions": 10,
    "reference_volatility_sessions": 60,
    "recent_to_reference_volatility_max_ratio": 0.95,
    "volume_20_prior_sessions_multiple": 1.20,
    "avg_daily_traded_value_20_prior_rs": 50_000_000,
    "rsi_14_min": 54.0,
    "rsi_14_max": 74.0,
    "atr_pct_min": 0.012,
    "atr_pct_max": 0.070,
    "max_close_over_ema20": 1.12,
    "close_position_in_daily_range_min": 0.70,
    "min_initial_stop_pct": 0.02,
    "max_initial_stop_pct": 0.10,
    "initial_stop_atr_multiple": 2.0,
    "initial_stop_lookback_sessions": 10,
    "max_next_session_entry_gap_above_signal_close": 0.01,
    "partial_profit_target_r_multiple": 2.0,
    "final_profit_target_r_multiple": 3.0,
    "max_holding_sessions": 100,
    "illustrative_risk_per_independent_stock_account": 0.01,
}


def _check_benchmark(levels):
    if not isinstance(levels, pd.Series):
        raise ValueError("NIFTY_50_TRI_SERIES_REQUIRED")
    if (not isinstance(levels.index, pd.DatetimeIndex)
            or levels.index.tz is not None
            or levels.index.has_duplicates or not levels.index.is_monotonic_increasing
            or len(levels)==0):
        raise ValueError("NIFTY_50_TRI_DATES_INVALID")
    values=pd.to_numeric(levels, errors="coerce")
    if not np.isfinite(values.to_numpy(dtype=float)).all() or (values <= 0).any():
        raise ValueError("NIFTY_50_TRI_LEVELS_INVALID")
    return pd.Series(values.to_numpy(dtype=float), index=levels.index)


def _check_stock(bars):
    needed={"Date","Open","High","Low","Close","Volume"}
    if not isinstance(bars,pd.DataFrame) or not needed.issubset(bars.columns):
        raise ValueError("OHLCV_FIELDS_REQUIRED")
    if bars.empty:
        raise ValueError("OHLCV_NONEMPTY_REQUIRED")
    dates=pd.DatetimeIndex(pd.to_datetime(bars.Date,errors="raise"))
    if (dates.tz is not None or dates.has_duplicates
            or not dates.is_monotonic_increasing):
        raise ValueError("OHLCV_CHRONOLOGY_INVALID")
    fields=bars[["Open","High","Low","Close","Volume"]].apply(
        pd.to_numeric,errors="raise"
    )
    if (not np.isfinite(fields.to_numpy(dtype=float)).all()
            or (fields[["Open","High","Low","Close"]] <= 0).any().any()
            or (fields.Volume < 0).any()
            or (fields.High < fields[["Open","Low","Close"]].max(axis=1)).any()
            or (fields.Low > fields[["Open","High","Close"]].min(axis=1)).any()):
        raise ValueError("OHLCV_PRICES_INVALID")
    return bars.reset_index(drop=True).copy()


def quality_leader_features(bars, nifty_50_tri):
    """Causal EOD signal + prior-10-low/ATR stop (not trading advice)."""
    clean=_check_stock(bars)
    benchmark=_check_benchmark(nifty_50_tri)
    d=features(clean)
    c,h,l,v=d.Close,d.High,d.Low,d.Volume
    e20,e50,e200=(ema(c,n) for n in (20,50,200))
    benchmark_ema50=ema(benchmark,50)
    benchmark_ema200=ema(benchmark,200)
    bench_return63=benchmark/benchmark.shift(63)-1
    # Reindex by exact EOD date. No timezone mismatch or forward-fill.
    dates=pd.DatetimeIndex(d.Date)
    bench_close=pd.Series(benchmark.reindex(dates).to_numpy(),index=d.index)
    bench_50=pd.Series(benchmark_ema50.reindex(dates).to_numpy(),index=d.index)
    bench_200=pd.Series(benchmark_ema200.reindex(dates).to_numpy(),index=d.index)
    bench_prev_200=pd.Series(
        benchmark_ema200.shift(PARAMETERS["benchmark_slope_sessions"])
        .reindex(dates).to_numpy(),index=d.index
    )
    bench_63=pd.Series(bench_return63.reindex(dates).to_numpy(),index=d.index)
    # No relative-return calculation for a stock day whose exact TRI level
    # is missing, even when price data and all other indicators exist.
    ret63=c/c.shift(63)-1
    relative63=ret63-bench_63
    vol=c.pct_change()
    sd10=vol.shift(1).rolling(
        PARAMETERS["recent_volatility_sessions"]
    ).std()
    sd60=vol.shift(1).rolling(
        PARAMETERS["reference_volatility_sessions"]
    ).std()
    past_volume_avg=v.shift(1).rolling(20).mean()
    past_turnover_avg=(c*v).shift(1).rolling(20).mean()
    past_breakout_high=h.shift(1).rolling(
        PARAMETERS["breakout_prior_sessions"]
    ).max()
    position_in_bar=(c-l)/(h-l).replace(0,np.nan)
    initial_stop=pd.concat([
        l.shift(1).rolling(PARAMETERS["initial_stop_lookback_sessions"]).min(),
        c-PARAMETERS["initial_stop_atr_multiple"]*d.ATR,
    ],axis=1).max(axis=1)
    d["Stop"]=initial_stop.where(initial_stop < c,np.nan)
    stop_pct=(c-d.Stop)/c
    d["TRI_Exact_Day_Present"]=bench_close.notna()
    d["Relative_TRI_63d_Return"]=relative63
    d["Initial_Stop_Distance_Pct"]=stop_pct
    d["Signal"] = (
        (len(d) >= PARAMETERS["min_bars"])
        & d["TRI_Exact_Day_Present"]
        & (bench_close > bench_200)
        & (bench_50 > bench_200)
        & (bench_200 > bench_prev_200)
        & (bench_63 > 0)
        & (ret63 >= PARAMETERS["stock_return_63_min"])
        & (relative63 >= PARAMETERS["relative_to_tri_return_63_min"])
        & (c > e20) & (e20 > e50) & (e50 > e200)
        & (e200 > e200.shift(PARAMETERS["stock_ema200_slope_sessions"]))
        & (c <= PARAMETERS["max_close_over_ema20"] * e20)
        & (c > past_breakout_high)
        & (v >= PARAMETERS["volume_20_prior_sessions_multiple"] * past_volume_avg)
        & (past_turnover_avg >= PARAMETERS["avg_daily_traded_value_20_prior_rs"])
        & d.RSI.between(PARAMETERS["rsi_14_min"],PARAMETERS["rsi_14_max"])
        & (d.ATR/c).between(
            PARAMETERS["atr_pct_min"],PARAMETERS["atr_pct_max"]
        )
        & (sd60 > 0)
        & (sd10 <= (
            PARAMETERS["recent_to_reference_volatility_max_ratio"]*sd60
        ))
        & (position_in_bar >= PARAMETERS["close_position_in_daily_range_min"])
        & (d.Stop > 0)
        & stop_pct.between(
            PARAMETERS["min_initial_stop_pct"],PARAMETERS["max_initial_stop_pct"]
        )
    ).fillna(False).astype(bool)
    return d


def model_plan_at_close(d):
    """Informational research plan ONLY; actual next-open fill may not occur."""
    if d.empty or not bool(d.iloc[-1]["Signal"]):
        return None
    row=d.iloc[-1]
    close=float(row.Close)
    stop=float(row.Stop)
    risk=close-stop
    if not all(map(math.isfinite,(close,stop,risk))) or not 0<stop<close:
        return None
    return {
        "signal_day":str(pd.Timestamp(row.Date).date()),
        "setup":STRATEGY_ID,
        "hypothetical_reference_entry_at_signal_close":round(close,4),
        "minimum_next_open_no_chase_fill":round(close,4),
        "maximum_next_open_no_chase_fill":round(
            close*(1+PARAMETERS["max_next_session_entry_gap_above_signal_close"]),4
        ),
        "indicative_initial_stop":round(stop,4),
        "indicative_target_2r_from_signal_close":round(close+2*risk,4),
        "indicative_target_3r_from_signal_close":round(close+3*risk,4),
        "max_holding_sessions":PARAMETERS["max_holding_sessions"],
        "position_risk_fraction_model":PARAMETERS[
            "illustrative_risk_per_independent_stock_account"
        ],
        "next_open_execution_prices_will_differ":True,
        "fundamental_point_in_time_verified":False,
        "independent_early_warning_to_buy":False,
        "win_probability":None,
        "live_eligible":False,
        "orders":"DISABLED",
    }
