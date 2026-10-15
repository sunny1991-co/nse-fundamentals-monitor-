#!/usr/bin/env python3
"""Conservative NSE equity swing candidate screener. Requires licensed, timestamped OHLCV CSV.
No live-feed claims, no calibrated probabilities, and no orders are placed.
Input: market_ohlcv.csv columns Symbol,Date,Open,High,Low,Close,Volume.
Optional: final_fundamental_shortlist.csv (Symbol). Missing shortlist -> no candidates.
"""
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent
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
    return 100 - 100 / (1 + rs)

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

def main():
    if not OHLCV.exists():
        raise SystemExit("No OHLCV feed: provide market_ohlcv.csv; no real-time prices are invented.")
    if not APPROVED.exists():
        raise SystemExit("Annual + quarterly verified final shortlist unavailable; no trading candidates.")
    approved = pd.read_csv(APPROVED,dtype=str)
    if "Symbol" not in approved:
        raise SystemExit("Verified shortlist must contain Symbol.")
    symbols = set(approved.Symbol.dropna().str.strip().str.upper())
    raw = pd.read_csv(OHLCV)
    required = {"Symbol","Date","Open","High","Low","Close","Volume"}
    if not required.issubset(raw):
        raise SystemExit(f"Missing OHLCV columns: {sorted(required-set(raw))}")
    raw["Symbol"] = raw.Symbol.astype(str).str.strip().str.upper()
    raw["Date"] = pd.to_datetime(raw.Date,errors="coerce")
    for col in ["Open","High","Low","Close","Volume"]:
        raw[col] = pd.to_numeric(raw[col],errors="coerce")
    raw = raw[raw.Symbol.isin(symbols)].sort_values(["Symbol","Date"])
    today = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).normalize()
    results=[]
    for sym,d in raw.groupby("Symbol"):
        d=d.drop_duplicates("Date",keep="last").dropna(subset=["Date","Open","High","Low","Close","Volume"]).copy()
        if len(d)<220 or d.Date.max()>today or (today-d.Date.max()).days>5:
            continue
        if ((d[["Open","High","Low","Close"]]<=0).any().any() or (d.Volume<0).any()
            or (d.High<d.Low).any() or (d.High<d[["Open","Close"]].max(axis=1)).any()
            or (d.Low>d[["Open","Close"]].min(axis=1)).any()):
            continue
        c=d.Close; v=d.Volume
        e20,e50,e200=ema(c,20),ema(c,50),ema(c,200)
        r=rsi(c); a,adx=atr_adx(d)
        macd=ema(c,12)-ema(c,26); signal=ema(macd,9)
        volratio=v.iloc[-1]/v.tail(20).mean() if v.tail(20).mean()>0 else 0
        last=float(c.iloc[-1]); risk=float(a.iloc[-1])
        if not np.isfinite([e20.iloc[-1],e50.iloc[-1],e200.iloc[-1],r.iloc[-1],adx.iloc[-1],risk,signal.iloc[-1]]).all():
            continue
        # No lookahead: breakout compares today against previous 20 completed sessions.
        breakout=last>float(d.High.shift(1).rolling(20).max().iloc[-1])
        trend=last>e20.iloc[-1]>e50.iloc[-1]>e200.iloc[-1]
        momentum=50<=r.iloc[-1]<=72 and macd.iloc[-1]>signal.iloc[-1] and adx.iloc[-1]>=20
        liquid=(c*v).tail(20).mean()>=5e7  # INR 5 crore daily turnover
        if not (trend and momentum and liquid and volratio>=1.3 and breakout and risk>0):
            continue
        entry=last
        stop=min(float(d.Low.tail(10).min()),entry-2*risk)
        per_share=entry-stop
        if per_share<=0 or per_share/entry>0.12:
            continue
        t1=entry+2*per_share; t2=entry+3*per_share
        score=sum([trend,momentum,breakout,volratio>=2,adx.iloc[-1]>=25,r.iloc[-1]<=65])
        results.append([sym,str(d.Date.iloc[-1].date()),round(last,2),round(entry,2),round(stop,2),
                        round(t1,2),round(t2,2),2.0,round(float(r.iloc[-1]),2),
                        round(float(adx.iloc[-1]),2),round(risk,2),round(float(volratio),2),
                        int(score),"20-day breakout; trend and momentum confirmed","EOD only; uncalibrated setup score"])
    pd.DataFrame(results,columns=COLS).sort_values("Trend_Score",ascending=False).to_csv(OUTPUT,index=False)
    print(f"Saved {len(results)} uncalibrated EOD swing candidates. No live data or win probability claimed.")

if __name__=="__main__":
    main()
