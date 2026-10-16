#!/usr/bin/env python3
"""Conservative NSE equity swing candidate screener. Requires licensed, timestamped OHLCV CSV.
No live-feed claims, no calibrated probabilities, and no orders are placed.
Input: market_ohlcv.csv columns Symbol,Date,Open,High,Low,Close,Volume.
Optional: final_fundamental_shortlist.csv (Symbol). Missing shortlist -> no candidates.
"""
from research_monitor.paths import data_root
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = data_root()
OHLCV = ROOT / "market_ohlcv.csv"
APPROVED = ROOT / "final_fundamental_shortlist.csv"
OUTPUT = ROOT / "swing_trade_candidates.csv"
COLS = ["Symbol","As_Of","Close","Entry","Stop_Loss","Target_1","Target_2","Risk_Reward_1","RSI_14","ADX_14","ATR_14","Volume_Ratio","Trend_Score","Setup","Data_Status"]

def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()

def rsi(s, n=14):
    d = s.diff()
    gain = d.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.mask((loss == 0) & (gain > 0), 100).mask((loss == 0) & (gain == 0), 50)

def atr_adx(d, n=14):
    h,l,c = d.High,d.Low,d.Close
    up,down = h.diff(), -l.diff()
    pdm = up.where((up>down)&(up>0),0.0)
    mdm = down.where((down>up)&(down>0),0.0)
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    a = tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    pdi = 100*pdm.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/a.replace(0,np.nan)
    mdi = 100*mdm.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/a.replace(0,np.nan)
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return a,dx.ewm(alpha=1/n,adjust=False,min_periods=n).mean()


