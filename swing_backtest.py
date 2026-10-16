"""Fixed-rule technical backtest, not a calibrated prediction of future returns.

Current fundamentals are NOT applied retrospectively. This tests technical rules
per stock; it does not establish a point-in-time fundamental strategy backtest.
Require provider metadata confirming corporate-action-adjusted daily OHLCV.
"""
from research_monitor.paths import data_root
from pathlib import Path
import numpy as np
import pandas as pd
from swing_strategy import ema, rsi, atr_adx

ROOT = data_root()
VERSION = "breakout-v1"
COST = 0.002  # illustrative all-in costs per side; not a broker tariff
SLIP = 0.001
MAX_HOLD = 100

def features(d):
    d = d.copy().reset_index(drop=True)
    c, v = d.Close, d.Volume
    d['ATR'], d['ADX'] = atr_adx(d)
    d['RSI'] = rsi(c)
    e20, e50, e200 = ema(c,20), ema(c,50), ema(c,200)
    macd = ema(c,12)-ema(c,26)
    d['Stop'] = np.minimum(d.Low.rolling(10).min(), c-2*d.ATR)
    d['Signal'] = ((c>e20)&(e20>e50)&(e50>e200)&d.RSI.between(50,72)
        &(macd>ema(macd,9))&(d.ADX>=20)&(c>d.High.shift().rolling(20).max())
        &(v>=1.3*v.rolling(20).mean())&((c*v).rolling(20).mean()>=5e7)
        &(d.ATR>0)&((c-d.Stop)/c<=.12)&(d.Stop>0))
    return d

def simulate(d, start, end, cost=COST, slip=SLIP, liquidate_at_end=True, with_state=False):
    """Next-open entry; stop first if a daily bar touches both stop and target.
    50% at 2R, rest at 3R or trailing stop; trailing updates for next bar only.
    Equity includes open position marks; gaps use open price, never stop price.
    """
    cash, position = 1.0, None
    curve, trades = [cash], []
    for i in range(start, end):
        b = d.iloc[i]
        if position is None and i >= 220 and bool(d.iloc[i-1].Signal):
            sig = d.iloc[i-1]
            fill = float(b.Open)*(1+slip)
            if sig.Close <= fill <= sig.Close*1.01 and 0 < fill-sig.Stop <= .12*fill:
                qty = cash/(fill*(1+cost))
                position = dict(entry=fill, stop=float(sig.Stop), qty=qty, original=qty,
                    before=cash, received=0., t1=fill+2*(fill-sig.Stop),
                    t2=fill+3*(fill-sig.Stop), partial=False, i=i,
                    entry_date=str(b.Date.date()))
                cash = 0.
        if position is not None:
            p = position
            reason, sell = None, None
            if b.Low <= p['stop']:
                sell, reason = min(float(b.Open),p['stop'])*(1-slip), 'stop'
            else:
                if not p['partial'] and b.High >= p['t1']:
                    amount = p['original']/2
                    proceeds = amount*p['t1']*(1-slip)*(1-cost)
                    cash += proceeds; p['received'] += proceeds; p['qty'] -= amount
                    p['partial'] = True
                if b.High >= p['t2']:
                    sell, reason = p['t2']*(1-slip), 'target2'
                elif i-p['i']+1 >= MAX_HOLD:
                    sell, reason = float(b.Close)*(1-slip), 'time'
                elif i == end-1 and liquidate_at_end:
                    sell, reason = float(b.Close)*(1-slip), 'sample_end'
            if sell is not None:
                proceeds = p['qty']*sell*(1-cost)
                cash += proceeds
                trades.append(dict(Entry_Date=p['entry_date'], Exit_Date=str(b.Date.date()),
                    Entry=p['entry'], Exit=sell, Reason=reason,
                    Return=(p['received']+proceeds)/p['before']-1))
                position = None
            elif p['partial']:
                p['stop'] = max(p['stop'], p['entry'], float(b.Close-2*b.ATR))
        curve.append(cash+(position['qty']*float(b.Close)*(1-cost) if position else 0))
    returns = np.array([t['Return'] for t in trades],dtype=float)
    equity = np.array(curve)
    losses = -returns[returns<0].sum()
    stats = dict(Trades=len(trades), Completed_Trades=sum(t['Reason']!='sample_end' for t in trades),
        Win_Rate=float((returns>0).mean()) if len(returns) else 0.,
        Mean_Return=float(returns.mean()) if len(returns) else 0.,
        Profit_Factor=float(returns[returns>0].sum()/losses) if losses>0 else None,
        Max_Drawdown=float((1-equity/np.maximum.accumulate(equity)).max()),
        Total_Return=float(equity[-1]-1))
    if with_state:
        return stats, trades, dict(position=position, cash=cash, marked_equity=float(equity[-1]))
    return stats, trades

def load_market():
    from research_monitor.history import read_history
    return read_history(ROOT)


def main():
    # Remove obsolete success files before a missing-feed/error exit.
    for name in ['swing_backtest_report.csv','swing_backtest_trades.csv']:
        (ROOT/name).unlink(missing_ok=True)
    raw, digest = load_market()
    reports, trade_rows = [], []
    for sym, bars in raw.groupby('Symbol'):
        d = features(bars)
        if len(d)<1260:
            reports.append(dict(Symbol=sym, Backtest_Pass=False, Reason='LESS_THAN_1260_BARS'))
            continue
        cut = 220+int((len(d)-220)*.7)
        dev, _ = simulate(d,220,cut)
        hold, trades = simulate(d,cut,len(d))
        stress, _ = simulate(d,cut,len(d),cost=COST*2,slip=SLIP*2)
        half = cut+(len(d)-cut)//2
        first, _ = simulate(d,cut,half)
        second, _ = simulate(d,half,len(d))
        passed = (dev['Completed_Trades']>=20 and dev['Mean_Return']>0
            and hold['Completed_Trades']>=30 and hold['Mean_Return']>0
            and hold['Profit_Factor'] is not None and hold['Profit_Factor']>=1.5
            and hold['Max_Drawdown']<=.20 and stress['Mean_Return']>0
            and first['Completed_Trades']>=5 and second['Completed_Trades']>=5
            and first['Mean_Return']>0 and second['Mean_Return']>0)
        reports.append(dict(Symbol=sym, Backtest_Pass=passed, Strategy=VERSION,
            Data_SHA256=digest, Start=str(d.Date.iloc[220].date()),
            Holdout_Start=str(d.Date.iloc[cut].date()), End=str(d.Date.iloc[-1].date()),
            **hold, Stress_Mean_Return=stress['Mean_Return'],
            Reason='TECHNICAL_HOLDOUT_PASS_NOT_CALIBRATED_PROBABILITY' if passed else 'INSUFFICIENT_OR_WEAK_EVIDENCE'))
        trade_rows.extend(dict(Symbol=sym,**t) for t in trades)
    pd.DataFrame(reports,columns=None if reports else ['Symbol','Backtest_Pass']).to_csv(ROOT/'swing_backtest_report.csv',index=False)
    pd.DataFrame(trade_rows).to_csv(ROOT/'swing_backtest_trades.csv',index=False)
    print(f'Backtested {len(reports)} stocks; passed {sum(bool(r["Backtest_Pass"]) for r in reports)}. Technical evidence only.')

if __name__ == '__main__':
    main()

