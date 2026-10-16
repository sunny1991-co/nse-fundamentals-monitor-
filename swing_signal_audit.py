"""Read-only signal/entry upper-bound audit. No simulation, network or writes."""
import json
from collections import Counter
from research_monitor.history import read_history
from research_monitor.paths import data_root
from swing_backtest import features, signal_conditions, SLIP


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


def filter_audit(d, start, end):
    # Signal-day slice exactly matches entry_audit's prior-bar slice.
    masks=signal_conditions(d).iloc[start-1:end-1]
    remaining=masks.index.to_series().map(lambda _: True)
    result={}
    for name in masks:
        passed=masks[name]
        before=int(remaining.sum())
        remaining=remaining & passed
        result[name]=dict(independent_pass=int(passed.sum()),
                          remaining_after=int(remaining.sum()),
                          first_rejected=before-int(remaining.sum()))
    return result


def audit(raw, digest):
    totals, distribution = Counter(), Counter()
    example = []
    filters = {}
    quality = Counter()
    for symbol, bars in raw.groupby('Symbol'):
        totals['symbols'] += 1
        if len(bars) < 1260:
            totals['insufficient_history'] += 1
            continue
        d=features(bars)
        cut=220+int((len(d)-220)*.7)
        result=entry_audit(d,cut,len(d))
        for name, counts in filter_audit(d,cut,len(d)).items():
            filters.setdefault(name, Counter()).update(counts)
        observed=d.iloc[cut-1:len(d)-1]
        quality['zero_volume_signal_days'] += int(observed.Volume.eq(0).sum())
        quality['missing_RSI_days'] += int(observed.RSI.isna().sum())
        quality['missing_ADX_days'] += int(observed.ADX.isna().sum())
        quality['missing_ATR_days'] += int(observed.ATR.isna().sum())
        totals['history_eligible_symbols'] += 1
        for key,value in result.items(): totals[key] += value
        if result['signal_opportunities'] == 0: totals['zero_signal_symbols'] += 1
        if result['eligible_entry_upper_bound'] < 30: totals['cannot_reach_30_holdout_trades_even_ignoring_open_positions'] += 1
        upper=result['eligible_entry_upper_bound']
        distribution['0' if upper==0 else '1-9' if upper<10 else '10-29' if upper<30 else '30+'] += 1
        if len(example)<10: example.append(dict(symbol=symbol,**result))
    return dict(scope='READ_ONLY_SIGNAL_ENTRY_AUDIT', data_sha256=digest,
                totals=dict(totals), entry_upper_bound_distribution=dict(distribution),
                filter_counts={name:dict(counts) for name,counts in filters.items()},
                input_indicator_checks=dict(quality),
                examples_first_10_alphabetically=example,
                filter_note='Independent pass counts overlap; first_rejected depends on listed filter order. This inspects the old holdout: any redesigned strategy needs separate untouched evaluation data.', 
                note='Entry rejection counts overlap. Upper bounds are NOT trades, returns or live signals. Strategy and thresholds unchanged.')


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--filters-only',action='store_true')
    args=parser.parse_args()
    raw,digest=read_history(data_root())
    result=audit(raw,digest)
    if args.filters_only:
        print('History SHA256:',digest)
        print('Holdout signal-day observations:',result['totals'].get('bars',0))
        print('Filter | independent pass | cumulative pass | first rejected')
        for name,counts in result['filter_counts'].items():
            print(name,counts['independent_pass'],counts['remaining_after'],counts['first_rejected'],sep=' | ')
        print('Input checks:',json.dumps(result['input_indicator_checks']))
        print(result['filter_note'])
    else:
        print(json.dumps(result,indent=2))
