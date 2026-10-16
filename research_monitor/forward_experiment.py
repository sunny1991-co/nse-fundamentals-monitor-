"""Three frozen technical hypotheses, automatic EOD forward observation only.

No fundamental qualification, portfolio allocation, broker order or live alert.
Existing losing historical results remain exploratory, never validation.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd
from research_monitor import paper
from research_monitor.history import read_history
from research_monitor.paths import CODE_ROOT
from research_monitor.schedule import IST, atomic_status
from research_monitor.seven_strategy_rules import signals
from swing_backtest import features

STRATEGIES = ('trend-following-v1', 'ema-pullback-v1', 'vcp-breakout-v1')
VERSION = 'three-hypotheses-forward-v1'


def code_hash():
    names = ['research_monitor/forward_experiment.py',
             'research_monitor/seven_strategy_rules.py', 'swing_backtest.py',
             'swing_strategy.py', 'research_monitor/paper.py']
    return hashlib.sha256(b''.join((CODE_ROOT / p).read_bytes() for p in names)).hexdigest()


def verified_closed(calendars, day, now):
    try:
        cal = calendars[day]
        if (cal.get('verified') is not True or not cal.get('source') or
                not cal['valid_from'] <= day <= cal['valid_to']):
            return False
        session = cal['sessions'][day]
        opening = datetime.fromisoformat(session['open'])
        close = datetime.fromisoformat(session['close'])
        return (opening.tzinfo is not None and close.tzinfo is not None
                and opening < close <= now
                and opening.astimezone(IST).date().isoformat() == day
                and close.astimezone(IST).date().isoformat() == day)
    except (KeyError, TypeError, ValueError):
        return False


def register(experiment, raw, calendars, now, started):
    added = 0
    # Freeze before observing any post-enrollment signal. Registration on the
    # first run deliberately does not backfill yesterday's historical signals.
    with closing(paper.connect(experiment)) as db, db:
        for symbol, bars in raw.groupby('Symbol'):
            bars = bars.sort_values('Date').reset_index(drop=True)
            day = bars.Date.iloc[-1].date().isoformat()
            if len(bars) < 1260 or not verified_closed(calendars, day, now):
                continue
            close = datetime.fromisoformat(calendars[day]['sessions'][day]['close'])
            if close <= started or (now.astimezone(IST).date() - bars.Date.iloc[-1].date()).days > 4:
                continue
            d = features(bars)
            for strategy in STRATEGIES:
                if not bool(signals(d, strategy).iloc[-1]):
                    continue
                # A stock/strategy can have only one unresolved paper position.
                pending = db.execute("SELECT 1 FROM plans WHERE symbol=? AND status NOT IN ('CLOSED','SKIPPED_ENTRY','MISSED_ENTRY_REGISTRATION') LIMIT 1", (symbol + '|' + strategy,)).fetchone()
                if pending:
                    continue
                entry, stop = float(d.Close.iloc[-1]), float(d.Stop.iloc[-1])
                if not 0 < stop < entry or (entry-stop)/entry > .12:
                    continue
                identity = hashlib.sha256((VERSION+'|'+strategy+'|'+symbol+'|'+day).encode()).hexdigest()
                plan = dict(strategy=strategy, entry=entry, stop=stop,
                            history_rows=len(bars), history_hash=paper.digest_bars(bars),
                            scope='UNQUALIFIED_TECHNICAL_FORWARD_ONE_UNIT_NOT_PORTFOLIO')
                added += db.execute('INSERT OR IGNORE INTO plans VALUES(?,?,?,?,?,?,?)',
                    (identity, symbol+'|'+strategy, day, now.isoformat(), json.dumps(plan),
                     'WAITING_FOR_COMPLETED_SESSION', '{}')).rowcount
    return added


def run(root, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Timezone-aware clock required')
    root = Path(root)
    experiment = root / 'runtime' / 'experiments' / VERSION
    experiment.mkdir(parents=True, exist_ok=True)
    # One process at a time, including protocol enrollment and report writes.
    import fcntl
    with (experiment / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = dict(version=VERSION, checked_at=now.isoformat(),
                      strategies=list(STRATEGIES), live_eligible=False,
                      real_orders_sent=0, forward_validation_pass=False,
                      scope='TECHNICAL_EOD_RESEARCH_NOT_FUNDAMENTAL_QUALIFIED',
                      portfolio_simulation=False, new_registered=0)
        protocol_path = experiment / 'protocol.json'
        fingerprint = code_hash()
        if not protocol_path.exists():
            atomic_status(protocol_path, dict(started_at=now.isoformat(), code_sha256=fingerprint,
                strategies=list(STRATEGIES), selection='ALL_HISTORY_ELIGIBLE_SYMBOLS',
                cost_per_side=.002, slippage_per_side=.001,
                costs='ILLUSTRATIVE_NOT_BROKER_TARIFF', historical_results='NOT_VALIDATION'))
        try:
            protocol = json.loads(protocol_path.read_text())
            if protocol['code_sha256'] != fingerprint:
                raise ValueError('FROZEN_CODE_CHANGED_REQUIRES_NEW_EXPERIMENT_VERSION')
            started = datetime.fromisoformat(protocol['started_at'])
            if started.tzinfo is None or started > now:
                raise ValueError('INVALID_ENROLLMENT_CLOCK')
            raw, _ = read_history(root, today=now.astimezone(IST).date())
            try:
                calendars = json.loads((root / 'runtime' / 'calendar_history.json').read_text())
            except (OSError, ValueError):
                calendars = {}
            # Reuse the established stop-first, gap-aware, next-open model.
            # The ledger's keys include strategy to keep experiments independent.
            frames = []
            with closing(paper.connect(experiment)) as db:
                keys = [r[0] for r in db.execute('SELECT DISTINCT symbol FROM plans')]
            for strategy in STRATEGIES:
                symbols = [key.rsplit('|', 1)[0] for key in keys if key.endswith('|'+strategy)]
                frame = raw[raw.Symbol.isin(symbols)].copy()
                frame['Symbol'] = frame.Symbol + '|' + strategy
                frames.append(frame)
            observed = pd.concat(frames, ignore_index=True)
            yesterday = now.astimezone(IST).date() - timedelta(days=1)
            observed = observed[observed.Date <= pd.Timestamp(yesterday)]
            paper.reconcile(experiment, observed, calendars, now)
            result['new_registered'] = register(experiment, raw, calendars, now, started)
            report = paper.report(experiment, now)
            result.update(counts=report['counts'], plans=report['plans'],
                          state='FORWARD_RESEARCH_RUNNING' if calendars else 'WAITING_FOR_VERIFIED_CALENDAR',
                          enrolled_at=started.isoformat())
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result.update(state='BLOCKED', reason=str(exc)[:160])
        atomic_status(experiment / 'status.json', result)
        return result
