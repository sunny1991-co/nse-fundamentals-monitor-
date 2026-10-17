"""Frozen long-only NSE EQ relative-strength pullback-recovery *hypothesis*.

This is a different entry from a prior-30-day breakout: a market-leading stock
pulls toward its rising EMA20 while remaining above EMA50, then closes back
above EMA20 and the preceding day's high with recovering volume. EOD signals
only; possible fills occur NEXT session at the open in an external simulator.

Designed after older outcomes were inspected on 2026-10-17. All historical
data through 2026-10-16 are therefore retrospective, NEVER an independent
test, calibrated forecast, fundamental certification or trade permission.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research_monitor.quality_leader_rules import _check_stock, _check_benchmark
from swing_backtest import features
from swing_strategy import ema

STRATEGY_ID = "rs-pullback-recovery-v1"
FROZEN_ON = "2026-10-17"
PARAMETERS = {
    "warmup_sessions": 260,
    "benchmark_ema_fast": 50,
    "benchmark_ema_slow": 200,
    "stock_ema_fast": 20,
    "stock_ema_mid": 50,
    "stock_ema_slow": 200,
    "long_term_slope_sessions": 20,
    "relative_return_sessions": 126,
    "stock_126_return_min": 0.12,
    "stock_relative_to_nifty_tri_126_min": 0.08,
    "pullback_prior_sessions": 8,
    "pullback_low_vs_ema20_max": 1.01,
    "pullback_low_vs_ema50_min": 0.97,
    "dry_up_prior_sessions": 5,
    "dry_up_vs_prior_20_volume_max": 0.90,
    "recovery_vs_prior_20_volume_min": 1.10,
    "min_20_day_prior_turnover_rs": 50_000_000,
    "rsi_min": 48.0,
    "rsi_max": 69.0,
    "atr_pct_min": 0.010,
    "atr_pct_max": 0.060,
    "max_close_above_ema20_pct": 0.06,
    "min_stop_distance_pct": 0.02,
    "max_stop_distance_pct": 0.09,
    "atr_stop_multiple": 2.0,
    "entry_gap_max_above_signal_close": 0.01,
    "risk_fraction_per_independent_paper_account": 0.01,
    "max_holding_sessions": 100,
    "target1_r": 2.0,
    "target2_r": 3.0,
}


def rs_pullback_features(bars: pd.DataFrame, nifty_50_tri: pd.Series) -> pd.DataFrame:
    """No rolling-window uses a future bar; missing exact TRI dates fail shut."""
    clean = _check_stock(bars)
    tri = _check_benchmark(nifty_50_tri)
    d = features(clean)
    close, high, low, volume = d.Close, d.High, d.Low, d.Volume
    e20, e50, e200 = (ema(close, n) for n in (20, 50, 200))
    tri50, tri200 = ema(tri, 50), ema(tri, 200)
    dates = pd.DatetimeIndex(d.Date)
    # Reindex by exact bar date. No forward/back fill, nearest date, or price NIFTY.
    tri_level = pd.Series(tri.reindex(dates).to_numpy(), index=d.index)
    tri_ma50 = pd.Series(tri50.reindex(dates).to_numpy(), index=d.index)
    tri_ma200 = pd.Series(tri200.reindex(dates).to_numpy(), index=d.index)
    tri_ma200_prev = pd.Series(
        tri200.shift(PARAMETERS["long_term_slope_sessions"])
        .reindex(dates).to_numpy(), index=d.index
    )
    # A missing benchmark session *inside* the lookback must not silently
    # shorten the benchmark's 126-observation return horizon.
    complete_tri_window = tri_level.notna().rolling(
        PARAMETERS["warmup_sessions"], min_periods=PARAMETERS["warmup_sessions"]
    ).sum().eq(PARAMETERS["warmup_sessions"])
    tri_ret = tri_level / tri_level.shift(
        PARAMETERS["relative_return_sessions"]
    ) - 1
    stock_ret = close / close.shift(PARAMETERS["relative_return_sessions"]) - 1
    relative = stock_ret - tri_ret

    prior8_low = low.shift(1).rolling(PARAMETERS["pullback_prior_sessions"]).min()
    prior_ema20 = e20.shift(1)
    prior_ema50 = e50.shift(1)
    prior20_vol = volume.shift(1).rolling(20).mean()
    prior5_vol = volume.shift(1).rolling(PARAMETERS["dry_up_prior_sessions"]).mean()
    prior_turnover = (close * volume).shift(1).rolling(20).mean()
    prev_below = close.shift(1) < prior_ema20
    # A true recovery day crosses above yesterday's completed EMA20 and high.
    reclaim = (
        prev_below & (close > e20) & (close > high.shift(1))
        & (close <= e20 * (1 + PARAMETERS["max_close_above_ema20_pct"]))
    )
    # No Q4 or broad high breakout requirement: resume trend after shallow dip.
    stop = pd.concat([
        prior8_low, close - PARAMETERS["atr_stop_multiple"] * d.ATR,
    ], axis=1).max(axis=1)
    stop = stop.where((stop > 0) & (stop < close))
    stop_dist = (close - stop) / close
    d["Stop"] = stop
    d["TRI_Exact_Day_Present"] = tri_level.notna()
    d["TRI_Complete_260_Sessions"] = complete_tri_window
    d["Relative_TRI_126d_Return"] = relative
    d["Initial_Stop_Distance_Pct"] = stop_dist
    warmup = pd.Series(
        np.arange(len(d)) >= PARAMETERS["warmup_sessions"]-1, index=d.index
    )
    d["Signal"] = (
        warmup
        & d.TRI_Exact_Day_Present & d.TRI_Complete_260_Sessions
        & (tri_level > tri_ma200) & (tri_ma50 > tri_ma200)
        & (tri_ma200 > tri_ma200_prev)
        & (tri_ret > 0)
        & (e50 > e200) & (e200 > e200.shift(20))
        & (close > e50)
        & (stock_ret >= PARAMETERS["stock_126_return_min"])
        & (relative >= PARAMETERS["stock_relative_to_nifty_tri_126_min"])
        & (prior8_low <= PARAMETERS["pullback_low_vs_ema20_max"] * prior_ema20)
        & (prior8_low >= PARAMETERS["pullback_low_vs_ema50_min"] * prior_ema50)
        & reclaim
        & (prior20_vol > 0)
        & (prior5_vol <= PARAMETERS["dry_up_vs_prior_20_volume_max"] * prior20_vol)
        & (volume >= PARAMETERS["recovery_vs_prior_20_volume_min"] * prior20_vol)
        & (prior_turnover >= PARAMETERS["min_20_day_prior_turnover_rs"])
        & d.RSI.between(PARAMETERS["rsi_min"], PARAMETERS["rsi_max"])
        & (d.ATR / close).between(
            PARAMETERS["atr_pct_min"], PARAMETERS["atr_pct_max"]
        )
        & stop_dist.between(
            PARAMETERS["min_stop_distance_pct"], PARAMETERS["max_stop_distance_pct"]
        )
    ).fillna(False).astype(bool)
    return d


def illustrative_plan(features_frame):
    """Research-only descriptive plan; neither an order nor a success forecast."""
    if features_frame.empty or not bool(features_frame.iloc[-1].Signal):
        return None
    row = features_frame.iloc[-1]
    close, stop = float(row.Close), float(row.Stop)
    distance = close - stop
    return {
        "strategy": STRATEGY_ID,
        "signal_day": str(pd.Timestamp(row.Date).date()),
        "reference_close": close,
        "next_open_entry_min_before_slippage": close,
        "next_open_entry_max_before_slippage": close * 1.01,
        "indicative_stop": stop,
        "indicative_target1_at_close_plus_2r": close + 2 * distance,
        "indicative_target2_at_close_plus_3r": close + 3 * distance,
        "relative_tri_126": float(row.Relative_TRI_126d_Return),
        "risk_fraction_paper_only": .01,
        "max_holding_sessions": 100,
        "win_probability": None,
        "fundamentals_point_in_time_verified": False,
        "live_eligible": False,
        "orders": "DISABLED",
    }
