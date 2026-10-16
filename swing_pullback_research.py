"""Bounded exploratory comparison; never supplies candidates, alerts or orders."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
from datetime import datetime, timezone

import pandas as pd

from research_monitor.history import read_history
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status
from swing_strategy import ema
from swing_backtest import features, simulate, COST, SLIP, VERSION
from research_monitor.exploratory_ranking import rank_exploratory

PROTOCOL = {
    'version': 'trend-pullback-research-v1',
    'design_date': '2026-10-16',
    'exploration_end': '2026-10-15',
    'hypothesis': 'EMA20 reclaim during a longer uptrend can produce more observations than a fresh 20-day breakout.',
    'signal_rules': [
        'close > EMA50 > EMA200',
        'previous close <= previous EMA20 and current close > current EMA20',
        'RSI14 between 50 and 72 inclusive',
        'volume >= trailing 20-bar average including signal day',
        'trailing mean(close*volume,20) >= 50000000 INR',
        'ATR14 > 0; stop=min(trailing 10-bar low, close-2*ATR14)',
        'stop > 0 and (close-stop)/close <= 0.12',
    ],
    'entry_exit': 'Existing breakout-v1 next-open band, stop-first, partial targets and 100-session limit; unchanged.',
    'minimum_history_bars': 1260,
    'comparison_window': 'Last 30% after 220 warm-up bars; already inspected, NOT untouched holdout.',
    'sample_order': 'SHA256 of symbol, ascending; independent of observed performance.',
    'cost_per_side': COST,
    'slippage_per_side': SLIP,
    'stress_multiplier': 2,
    'live_eligible': False,
}


def pullback_features(bars):
    d=features(bars)
    c,v=d.Close,d.Volume
    e20,e50,e200=ema(c,20),ema(c,50),ema(c,200)
    d['Signal']=((c>e50)&(e50>e200)&(c.shift()<=e20.shift())&(c>e20)
        &d.RSI.between(50,72)&(v>=v.rolling(20).mean())
        &((c*v).rolling(20).mean()>=5e7)&(d.ATR>0)
        &((c-d.Stop)/c<=.12)&(d.Stop>0))
    return d



def completed_trade_stats(trades):
    """Net trade returns excluding censored/forced sample-end liquidations.

    This is descriptive research, not a significance test or strategy approval.
    Unknown exit reasons are not presumed to be completed positions.
    """
    returns = [float(t['Return']) for t in trades
               if t.get('Reason') not in (None, 'sample_end')]
    if not returns:
        return dict(count=0, mean_net_return=None, profit_factor=None)
    losses = -sum(r for r in returns if r < 0)
    gains = sum(r for r in returns if r > 0)
    return dict(count=len(returns), mean_net_return=sum(returns)/len(returns),
                profit_factor=gains/losses if losses > 0 else None)


def compare(raw, digest, limit=20):
    if not 1 <= limit <= 100:
        raise ValueError('limit must be 1..100 for a bounded exploratory run')
    # Never consume post-design observations in a retrospective tuning report.
    frame=raw[raw.Date<=pd.Timestamp(PROTOCOL['exploration_end'])]
    groups={symbol:bars for symbol,bars in frame.groupby('Symbol') if len(bars)>=1260}
    symbols=sorted(groups,key=lambda s:hashlib.sha256(s.encode()).hexdigest())[:limit]
    rows=[]
    pooled = {version: {"base": [], "stress": [], "traded_symbols": set()}
              for version in (VERSION, PROTOCOL["version"])}
    for symbol in symbols:
        bars=groups[symbol]
        start=220+int((len(bars)-220)*.7)
        for version, build in [(VERSION,features),(PROTOCOL['version'],pullback_features)]:
            d=build(bars)
            base,base_trades=simulate(d,start,len(d))
            stress,stress_trades=simulate(d,start,len(d),cost=COST*2,slip=SLIP*2)
            completed=completed_trade_stats(base_trades)
            stress_completed=completed_trade_stats(stress_trades)
            # Keep the original costs/slippage and exclude forced sample-end
            # liquidations when ranking completed observations.
            base_returns=[float(t['Return']) for t in base_trades
                          if t.get('Reason') not in (None, 'sample_end')]
            stress_returns=[float(t['Return']) for t in stress_trades
                            if t.get('Reason') not in (None, 'sample_end')]
            pooled[version]['base'].extend(base_returns)
            pooled[version]['stress'].extend(stress_returns)
            if base_returns:
                pooled[version]['traded_symbols'].add(symbol)
            rows.append(dict(symbol=symbol,strategy=version,
                start=str(d.Date.iloc[start].date()),end=str(d.Date.iloc[-1].date()),
                signal_opportunities=int(d.Signal.iloc[start-1:len(d)-1].sum()),
                completed_trades=base['Completed_Trades'],
                sample_end_liquidations=base['Trades']-base['Completed_Trades'],
                completed_mean_net_trade_return=completed['mean_net_return'],
                stress_completed_mean_net_trade_return=stress_completed['mean_net_return'],
                completed_profit_factor=completed['profit_factor'],
                stress_completed_profit_factor=stress_completed['profit_factor'],
                mean_trade_return_including_sample_end=base['Mean_Return'],
                max_single_stock_drawdown=base['Max_Drawdown'],
                stress_mean_trade_return_including_sample_end=stress['Mean_Return']))
    summaries={}
    for version in [VERSION,PROTOCOL['version']]:
        selected=[r for r in rows if r['strategy']==version]
        combined=completed_trade_stats([
            {'Return': ret, 'Reason': 'completed'}
            for ret in pooled[version]['base']
        ])
        combined_stress=completed_trade_stats([
            {'Return': ret, 'Reason': 'completed'}
            for ret in pooled[version]['stress']
        ])
        summaries[version]=dict(
            stocks=len(selected),
            pooled_completed_trades=combined['count'],
            pooled_stress_completed_trades=combined_stress['count'],
            stocks_with_completed_trades=len(pooled[version]['traded_symbols']),
            pooled_completed_mean_net_return=combined['mean_net_return'],
            pooled_stress_completed_mean_net_return=combined_stress['mean_net_return'],
            pooled_completed_profit_factor=combined['profit_factor'],
            worst_single_stock_drawdown=max(
                (r['max_single_stock_drawdown'] for r in selected),default=None),
            completed_trades=sum(r['completed_trades'] for r in selected),
            stocks_without_completed_trades=sum(r['completed_trades']==0 for r in selected),
            median_completed_trades=median([r['completed_trades'] for r in selected]) if selected else None,
            signal_opportunities=sum(r['signal_opportunities'] for r in selected),
            stocks_with_completed_trades_and_positive_mean=sum(r['completed_trades']>0 and r['mean_trade_return_including_sample_end']>0 for r in selected),
            stocks_with_completed_trades_and_positive_stress_mean=sum(r['completed_trades']>0 and r['stress_mean_trade_return_including_sample_end']>0 for r in selected),
            stocks_with_positive_completed_mean=sum(r['completed_mean_net_trade_return'] is not None and r['completed_mean_net_trade_return']>0 for r in selected),
            stocks_with_positive_completed_mean_under_stress=sum(r['stress_completed_mean_net_trade_return'] is not None and r['stress_completed_mean_net_trade_return']>0 for r in selected),
            stocks_with_at_least_30_completed_trades=sum(r['completed_trades']>=30 for r in selected))
    source=Path(__file__).read_bytes()
    engine=Path(__file__).with_name('swing_backtest.py').read_bytes()
    indicators=Path(__file__).with_name('swing_strategy.py').read_bytes()
    return dict(scope='EXPLORATORY_COMPARISON_NOT_STRATEGY_VALIDATION',
        created_at=datetime.now(timezone.utc).isoformat(),data_sha256=digest,
        protocol=PROTOCOL,
        protocol_sha256=hashlib.sha256(json.dumps(PROTOCOL,sort_keys=True).encode()).hexdigest(),
        implementation_sha256=hashlib.sha256(source+engine+indicators).hexdigest(),
        eligible_universe=len(groups),sample_symbols=symbols,
        summary=summaries,stock_observations=rows,
        research_selection=rank_exploratory(summaries),
        validation_status='NOT_VALIDATED',live_eligible=False,
        limitations=['Fixed bounded sample, not whole-universe performance.',
          'Current surviving universe; no point-in-time fundamentals or portfolio simulation.',
          'Original mean_trade_return fields include forced sample-end liquidation; completed_mean_net_trade_return fields exclude them and are descriptive only.',
          'All returns use illustrative per-side trading costs; profit factor with no losing completed trades is reported as null.',
          'Already inspected history cannot certify a revised strategy; separate untouched evaluation or forward paper evidence required.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit',type=int,default=20)
    args=parser.parse_args()
    if not 1<=args.limit<=100: parser.error('--limit must be 1..100')
    root=data_root()
    raw,digest=read_history(root)
    result=compare(raw,digest,args.limit)
    path=root/'runtime'/'experiments'/PROTOCOL['version']/'comparison.json'
    atomic_status(path,result)
    print(json.dumps({key:result[key] for key in (
        'scope','eligible_universe','sample_symbols','summary',
        'research_selection','validation_status','live_eligible')},indent=2))
    print('Detailed research report:',path)


if __name__=='__main__':
    main()
