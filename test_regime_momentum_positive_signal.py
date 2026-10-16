"""Constructed EOD positive-signal fixture: proves rule set is not always false.

Uses synthetic bars and controlled RSI/ATR/stop columns only. NOT market validation.
"""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research_monitor.regime_momentum_rules import regime_features


class RegimePositiveSignalTests(unittest.TestCase):
    def test_healthy_broad_market_momentum_and_breakout_can_signal(self):
        n=400
        idx=np.arange(n,dtype=float)
        close=100+idx*.2
        # Strong older daily price variance, then quiet consolidation.
        close[n-75:n-20] += 2.5 * np.where(
            np.arange(55)%2 == 0,1.,-1.
        )
        close[-1] += 2.0
        frame=pd.DataFrame({
            "Symbol":["SYNTH"]*n,
            "Date":pd.date_range("2020-01-01",periods=n),
            "Open":close.copy(),
            "High":close+0.2,
            "Low":close-0.2,
            "Close":close.copy(),
            "Volume":np.full(n,1_000_000.),
        })
        market=pd.DataFrame({
            "Breadth":np.full(n,.70),
            "Breadth_Stocks":np.full(n,250),
            "Momentum_Stocks":np.full(n,250),
        },index=pd.DatetimeIndex(frame.Date))
        ranks=pd.Series(
            np.full(n,.90),
            index=pd.MultiIndex.from_arrays([
                ["SYNTH"]*n,pd.DatetimeIndex(frame.Date)
            ],names=["Symbol","Date"])
        )
        def controlled_features(bars):
            d=bars.copy().reset_index(drop=True)
            d["ATR"]=d.Close*.025
            d["RSI"]=62.0
            d["Stop"]=d.Close*.95
            d["Signal"]=False
            return d
        with patch("research_monitor.regime_momentum_rules.features",
                   side_effect=controlled_features):
            valid=regime_features(frame,"SYNTH",market,ranks)
            self.assertTrue(bool(valid.Signal.iloc[-1]))
            # Regime disabled: exact same technical bar must NOT signal.
            market_bad=market.copy()
            market_bad.loc[frame.Date.iloc[-1],"Breadth"]=.49
            blocked=regime_features(frame,"SYNTH",market_bad,ranks)
            self.assertFalse(bool(blocked.Signal.iloc[-1]))


if __name__=="__main__":
    unittest.main()
