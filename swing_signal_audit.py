"""Read-only signal/entry upper-bound audit. No simulation, network or writes."""
import json
from collections import Counter
from research_monitor.history import read_history
from research_monitor.paths import data_root
from swing_backtest import features, SLIP


def entry_audit(d, start, end, slip=SLIP):
    # Match simulator's prior-bar signal and current-bar slipped open. Counting
    # every opportunity ignores occupied positions, so this is an upper bound.
    current = d.iloc[start:end].reset_index(drop=True)
    previous = d.iloc[start-1:end-1].reset_index(drop=True)
    signals = previous.Signal.fillna(False).astype(bool)
    fill = current.Open*(1+slip)
    below = fill < previous.Close
    chase = fill > previous.Close*1.01
    risk = ~((fill-previous.Stop > 0) & (fill-previous.Stop <= .12*fill))
    eligible = signals & ~below & ~chase & ~risk
    return dict(bars=len(current), signal_opportunities=int(signals.sum()),
                below_signal_close=int((signals & below).sum()),
                above_one_percent_band=int((signals & chase).sum()),
                invalid_or_excess_stop_distance=int((signals & risk).sum()),
                eligible_entry_upper_bound=int(eligible.sum()))


def audit(raw, digest):
    totals, distribution = Counter(), Counter()
    example = []
    for symbol, bars in raw.groupby('Symbol'):
        totals['symbols'] += 1
        if len(bars) < 1260:
            totals['insufficient_history'] += 1
            continue
        d=features(bars)
        cut=220+int((len(d)-220)*.7)
        result=entry_audit(d,cut,len(d))
        totals['history_eligible_symbols'] += 1
        for key,value in result.items(): totals[key] += value
        if result['signal_opportunities'] == 0: totals['zero_signal_symbols'] += 1
        if result['eligible_entry_upper_bound'] < 30: totals['cannot_reach_30_holdout_trades_even_ignoring_open_positions'] += 1
        upper=result['eligible_entry_upper_bound']
        distribution['0' if upper==0 else '1-9' if upper<10 else '10-29' if upper<30 else '30+'] += 1
        if len(example)<10: example.append(dict(symbol=symbol,**result))
    return dict(scope='READ_ONLY_SIGNAL_ENTRY_AUDIT', data_sha256=digest,
                totals=dict(totals), entry_upper_bound_distribution=dict(distribution),
                examples_first_10_alphabetically=example,
                note='Entry rejection counts overlap. Upper bounds are NOT trades, returns or live signals. Strategy and thresholds unchanged.')


if __name__ == '__main__':
    raw,digest=read_history(data_root())
    print(json.dumps(audit(raw,digest),indent=2))
