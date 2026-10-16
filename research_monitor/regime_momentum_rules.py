"""Frozen research-only relative-momentum and market-breadth swing hypothesis.

Never a live recommendation. All signals use known same-day closes; any order
would be no earlier than NEXT session. The current-historical eligible universe
is survivor-biased; point-in-time constituents are NOT available here.
No stop or return parameter is optimized from existing NSE backtests.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from swing_backtest import features
from swing_strategy import ema

STRATEGY_ID = "regime-momentum-confirmation-v1"

# Frozen prior to looking at this strategy's actual historical NSE outcomes.
PARAMETERS = {
    "lookback_momentum": 126,
    "min_126d_return": 0.10,
    "min_momentum_percentile": 0.70,
    "breadth_threshold": 0.55,
    "min_breadth_stocks": 100,
    "min_momentum_stocks": 100,
    "ema200_slope_bars": 20,
    "breakout_lookback": 15,
    "min_volume_multiple": 1.0,
    "min_avg_daily_turnover_rs": 50_000_000,
    "min_rsi": 52,
    "max_rsi": 75,
    "min_atr_pct": 0.012,
    "max_atr_pct": 0.065,
    "min_stop_distance": 0.025,
    "max_stop_distance": 0.10,
    "volatility_short": 20,
    "volatility_long": 60,
    "max_volatility_ratio": 1.05,
}


def make_market_context(groups):
    """Breadth and cross-sectional ranks, each day from same/past closes only.

    groups: {symbol: bars} prefiltered to completed daily adjusted OHLCV,
    and with sufficient historical bars. No forward fill, no future dates.
    Note: today's currently-surviving group membership is LOOKAHEAD-BIASED
    for historical universes, even though indicators themselves are causal.
    """
    pieces = []
    for symbol in sorted(groups):
        d = groups[symbol].reset_index(drop=True)
        close = d.Close.astype(float)
        e200 = ema(close, 200)
        up = (close > e200).astype(float).where(e200.notna())
        mom = close/close.shift(PARAMETERS["lookback_momentum"])-1
        pieces.append(pd.DataFrame({
            "Date": pd.to_datetime(d.Date).to_numpy(),
            "Symbol": symbol,
            "Above200": up.to_numpy(),
            "Momentum126": mom.to_numpy(),
        }))
    if not pieces:
        return (pd.DataFrame(columns=["Breadth", "Breadth_Stocks", "Momentum_Stocks"]),
                pd.Series(dtype=float, name="Momentum_Percentile"))
    all_rows = pd.concat(pieces, ignore_index=True)
    by_date = all_rows.groupby("Date", sort=True)
    breadth = by_date.agg(
        Breadth=("Above200", "mean"),
        Breadth_Stocks=("Above200", "count"),
        Momentum_Stocks=("Momentum126", "count"),
    )
    # pct rank is cross-sectional per close date; ties use average ranks.
    all_rows["Momentum_Percentile"] = by_date["Momentum126"].rank(
        pct=True, method="average"
    )
    ranks = all_rows.set_index(["Symbol", "Date"])["Momentum_Percentile"]
    return breadth, ranks


def regime_features(bars, symbol, breadth, momentum_ranks):
    """Return EOD signal with explicit missing-context rejection."""
    d = features(bars)
    d["Signal"] = False
    if len(d) < 220 or symbol is None or not isinstance(symbol, str):
        return d
    dates = pd.DatetimeIndex(d.Date)
    # Align by ROW, not by date index: raw features have RangeIndex.
    market = breadth.reindex(dates).reset_index(drop=True)
    if len(momentum_ranks):
        idx = pd.MultiIndex.from_arrays(
            [[symbol]*len(d), dates], names=["Symbol", "Date"]
        )
        percentile = pd.Series(
            momentum_ranks.reindex(idx).to_numpy(dtype=float), index=d.index
        )
    else:
        percentile = pd.Series(np.nan, index=d.index)
    c, h, v = d.Close, d.High, d.Volume
    e50 = ema(c, 50)
    e200 = ema(c, 200)
    ret126 = c/c.shift(PARAMETERS["lookback_momentum"])-1
    ret = c.pct_change()
    sd20 = ret.shift(1).rolling(PARAMETERS["volatility_short"]).std()
    sd60 = ret.shift(1).rolling(PARAMETERS["volatility_long"]).std()
    stop_distance = (c-d.Stop)/c

    # RSI, ATR and stop are causal existing features. Breakout must be against
    # the PRIOR high, not a window that accidentally contains today's high.
    signal = (
        (market.Breadth_Stocks >= PARAMETERS["min_breadth_stocks"])
        & (market.Momentum_Stocks >= PARAMETERS["min_momentum_stocks"])
        & (market.Breadth >= PARAMETERS["breadth_threshold"])
        & (percentile >= PARAMETERS["min_momentum_percentile"])
        & (ret126 >= PARAMETERS["min_126d_return"])
        & (c > e50) & (e50 > e200)
        & (e200 > e200.shift(PARAMETERS["ema200_slope_bars"]))
        & (c > h.shift(1).rolling(PARAMETERS["breakout_lookback"]).max())
        & (v >= PARAMETERS["min_volume_multiple"] * v.rolling(20).mean())
        & (c*v).rolling(20).mean().ge(
            PARAMETERS["min_avg_daily_turnover_rs"]
        )
        & d.RSI.between(PARAMETERS["min_rsi"], PARAMETERS["max_rsi"])
        & (d.ATR/c).between(
            PARAMETERS["min_atr_pct"], PARAMETERS["max_atr_pct"]
        )
        & (d.Stop > 0)
        & stop_distance.between(
            PARAMETERS["min_stop_distance"], PARAMETERS["max_stop_distance"]
        )
        & (sd60 > 0)
        & (sd20 <= PARAMETERS["max_volatility_ratio"] * sd60)
    )
    d["Signal"] = signal.fillna(False).astype(bool)
    return d
