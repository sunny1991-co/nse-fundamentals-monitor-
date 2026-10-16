"""Seven *fixed* equity swing hypotheses. Signals are close-confirmed only.

All signals are research-only. They reuse swing_backtest.features() and the
existing next-open fill, stops, costs and liquidation rules. No parameter
optimization, orders, alerts or candidates are permitted here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from swing_strategy import ema

STRATEGIES = (
    "trend-following-v1",
    "ema-pullback-v1",
    "momentum-breakout-v1",
    "vcp-breakout-v1",
    "relative-strength-v1",
    "mean-reversion-v1",
    "post-earnings-momentum-v1",
)

NEEDS_BENCHMARK = "relative-strength-v1"
NEEDS_EARNINGS = "post-earnings-momentum-v1"


def announcement_flags(dates, announced_dates):
    """Only closes 1..5 sessions AFTER a public announcement date are eligible."""
    dates = pd.DatetimeIndex(dates)
    result = np.zeros(len(dates), dtype=bool)
    for released in announced_dates:
        release = pd.Timestamp(released).normalize()
        first = dates.searchsorted(release, side="right")
        result[first:min(first + 5, len(dates))] = True
    return pd.Series(result, index=range(len(dates)))


def signals(d, strategy, *, benchmark=None, announcement_dates=None):
    """Return a boolean Signal series; no missing source can create signals.

    benchmark is an indexed historical NIFTY 50 TRI level series on session
    dates. No forward filling or price substitution is allowed.
    """
    if strategy not in STRATEGIES:
        raise ValueError("UNKNOWN_FROZEN_STRATEGY")
    c, h, l, v = d.Close, d.High, d.Low, d.Volume
    e20, e50, e200 = (ema(c, n) for n in (20, 50, 200))
    uptrend = (c > e50) & (e50 > e200)
    turnover = (c * v).rolling(20).mean() >= 5e7
    risk = ((d.ATR > 0) & (d.Stop > 0)
            & ((c - d.Stop) / c > 0)
            & ((c - d.Stop) / c <= .12))
    liquid = turnover & risk
    avg_vol = v.rolling(20).mean()
    prior20 = h.shift(1).rolling(20).max()
    prior55 = h.shift(1).rolling(55).max()

    if strategy == "trend-following-v1":
        signal = uptrend & (c > prior20) & d.RSI.between(50, 75)
    elif strategy == "ema-pullback-v1":
        signal = (uptrend & (c.shift(1) <= e20.shift(1)) & (c > e20)
                  & d.RSI.between(50, 72) & (v >= avg_vol))
    elif strategy == "momentum-breakout-v1":
        signal = ((c > e50) & (c > prior55) & d.RSI.between(55, 80)
                  & (v >= 1.5 * avg_vol))
    elif strategy == "vcp-breakout-v1":
        # Historical contraction windows exclude today's breakout session.
        width20 = (h.shift(1).rolling(20).max()
                   - l.shift(1).rolling(20).min()) / c.shift(1)
        width60 = (h.shift(1).rolling(60).max()
                   - l.shift(1).rolling(60).min()) / c.shift(1)
        volatility = c.pct_change().shift(1)
        vol20 = volatility.rolling(20).std()
        vol60 = volatility.rolling(60).std()
        signal = (uptrend & (c > prior20) & (v >= 1.3 * avg_vol)
                  & (width60 > 0) & (width20 <= 0.75 * width60)
                  & (vol60 > 0) & (vol20 <= 0.8 * vol60))
    elif strategy == "relative-strength-v1":
        if benchmark is None:
            return pd.Series(False, index=d.index)
        # Reindex strictly to the exact trading date, never forward fill.
        b = benchmark.reindex(pd.DatetimeIndex(d.Date))
        b = pd.Series(b.to_numpy(), index=d.index, dtype=float)
        valid = b.notna() & (b > 0) & b.shift(63).notna()
        valid &= b.shift(126).notna()
        outperform63 = (c / c.shift(63) - 1) - (b / b.shift(63) - 1)
        outperform126 = (c / c.shift(126) - 1) - (b / b.shift(126) - 1)
        signal = (valid & uptrend & (c > prior20)
                  & (outperform63 >= .08) & (outperform126 >= .10))
    elif strategy == "mean-reversion-v1":
        mid = c.rolling(20).mean()
        low_band = mid - 1.5 * c.rolling(20).std()
        signal = ((c > e200) & (e50 > e200) & (c > c.shift(1))
                  & (c.shift(1) < low_band.shift(1))
                  & (c < e20) & d.RSI.between(35, 55))
    else:
        if not announcement_dates:
            return pd.Series(False, index=d.index)
        eligible = announcement_flags(d.Date, announcement_dates)
        signal = (eligible & uptrend & (c > prior20)
                  & (v >= 1.5 * avg_vol) & d.RSI.between(50, 80))

    return (signal & liquid).fillna(False).astype(bool)
