"""Forward EOD research ledger. One normalized unit per stock, no real orders.

Plans must be recorded before the verified next session opens. No portfolio
allocation or future win probability is inferred from these observations.
"""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
from research_monitor.history import read_history
from research_monitor.readiness import candidate_rows
from research_monitor.schedule import IST, atomic_status
from swing_backtest import VERSION, features, simulate


def digest_bars(frame):
    return hashlib.sha256(frame[['Date','Open','High','Low','Close','Volume']].to_csv(index=False,date_format='%Y-%m-%d',float_format='%.12g').encode()).hexdigest()


def connect(root):
    path=Path(root)/'runtime'/'paper.sqlite'
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY,symbol TEXT,signal_day TEXT,registered_at TEXT,plan TEXT,status TEXT,state TEXT)')
    path.chmod(0o600)
    return db


def register(root, raw, now):
    rows, evidence=candidate_rows(root,now)
    if evidence != 'CONDITIONAL_RESEARCH_ONLY':
        return 0,evidence
    added=0
    with closing(connect(root)) as db, db:
        for candidate in rows:
            if candidate.get('Strategy') != VERSION:
                continue
            symbol=candidate.get('Symbol','')
            bars=raw[raw.Symbol.eq(symbol)].sort_values('Date').reset_index(drop=True)
            if bars.empty:
                continue
            day=str(bars.Date.iloc[-1].date())
            if candidate.get('Signal_Date') != day or not 0 <= (now.astimezone(IST).date()-bars.Date.iloc[-1].date()).days <= 4:
                continue
            d=features(bars)
            if len(d)<1260 or not bool(d.iloc[-1].Signal):
                continue
            entry=float(d.iloc[-1].Close);stop=float(d.iloc[-1].Stop)
            if not 0<stop<entry or (entry-stop)/entry>.12:
                continue
            identity=hashlib.sha256((VERSION+'|'+symbol+'|'+day).encode()).hexdigest()
            # Freeze exact strategy values, not display-rounded candidate prices.
            plan=dict(strategy=VERSION,entry=entry,stop=stop,history_rows=len(bars),
                      history_hash=digest_bars(bars),scope='NORMALIZED_ONE_UNIT_PER_STOCK_NOT_PORTFOLIO')
            added+=db.execute('INSERT OR IGNORE INTO plans VALUES(?,?,?,?,?,?,?)',
                (identity,symbol,day,now.isoformat(),json.dumps(plan),'WAITING_FOR_COMPLETED_SESSION','{}')).rowcount
    return added,'REGISTERED_RESEARCH_ONLY'


def expected_sessions(calendars, signal_day, last_day, now):
    """Every intervening day must be covered; absent days are not assumed holidays."""
    result=[];day=pd.Timestamp(signal_day).date()+timedelta(days=1)
    while day<=last_day:
        key=day.isoformat();cal=calendars.get(key,{})
        if (cal.get('verified') is not True or not cal.get('source') or
            not cal.get('valid_from','9999')<=key<=cal.get('valid_to','0000') or
            not isinstance(cal.get('sessions'),dict)):
            raise ValueError('CALENDAR_GAP_REQUIRES_VERIFICATION')
        session=cal['sessions'].get(key)
        if session is not None:
            opening=datetime.fromisoformat(session['open']);close=datetime.fromisoformat(session['close'])
            if (opening.tzinfo is None or close.tzinfo is None or opening>=close or
                opening.astimezone(IST).date()!=day or close.astimezone(IST).date()!=day or close>now):
                raise ValueError('SESSION_TIMING_NOT_VERIFIED')
            result.append((key,opening))
        day+=timedelta(days=1)
    return result


def reconcile(root, raw, calendars, now):
    updated=0
    with closing(connect(root)) as db, db:
        stored=db.execute('SELECT id,symbol,signal_day,registered_at,plan,status,state FROM plans').fetchall()
        for identity,symbol,signal_day,registered_at,plan_text,old_status,state_text in stored:
            if pd.Timestamp(signal_day).date() >= now.astimezone(IST).date():
                continue  # Today's newly registered plan has no completed next session.
            if old_status in ('SKIPPED_ENTRY','MISSED_ENTRY_REGISTRATION','SOURCE_REVISION_REVIEW_REQUIRED'):
                continue
            plan=json.loads(plan_text);prior=json.loads(state_text)
            bars=raw[raw.Symbol.eq(symbol)].sort_values('Date').reset_index(drop=True)
            prefix=bars[bars.Date<=pd.Timestamp(signal_day)]
            state=prior.copy()
            status='WAITING_FOR_COMPLETED_SESSION'
            if len(prefix)!=plan['history_rows'] or digest_bars(prefix)!=plan['history_hash']:
                status='SOURCE_REVISION_REVIEW_REQUIRED'
            elif prior.get('observed_through') and digest_bars(bars[bars.Date<=pd.Timestamp(prior['observed_through'])])!=prior.get('observed_hash'):
                status='SOURCE_REVISION_REVIEW_REQUIRED'
            else:
                if old_status == 'CLOSED':
                    continue
                try:
                    last_day=now.astimezone(IST).date()-timedelta(days=1)
                    sessions=expected_sessions(calendars,signal_day,last_day,now)
                    if sessions:
                        if datetime.fromisoformat(registered_at)>=sessions[0][1]:
                            status='MISSED_ENTRY_REGISTRATION'
                        else:
                            observed=bars[bars.Date>pd.Timestamp(signal_day)]
                            expected=[day for day,_ in sessions]
                            if [str(x.date()) for x in observed.Date]!=expected:
                                raise ValueError('COMPLETED_SESSION_BARS_MISSING_OR_UNEXPECTED')
                            d=features(bars);d['Signal']=False
                            i=len(prefix)-1
                            d.loc[i,['Signal','Close','Stop']]=[True,plan['entry'],plan['stop']]
                            stats,trades,position=simulate(d,i+1,len(d),liquidate_at_end=False,with_state=True)
                            status='OPEN' if position['position'] else ('CLOSED' if trades else 'SKIPPED_ENTRY')
                            state=dict(stats=stats,trades=trades,position=position,
                                observed_through=str(bars.Date.iloc[-1].date()),observed_hash=digest_bars(bars))
                except (ValueError,TypeError,KeyError,AttributeError):
                    status='WAITING_FOR_VERIFIED_CALENDAR_OR_COMPLETE_BARS'
            db.execute('UPDATE plans SET status=?,state=? WHERE id=?',(status,json.dumps(state),identity))
            updated+=1
    return updated


def report(root, now):
    with closing(connect(root)) as db:
        rows=db.execute('SELECT symbol,signal_day,registered_at,status,state FROM plans ORDER BY registered_at DESC').fetchall()
    counts={}
    plans=[]
    for symbol,day,stamp,status,state in rows:
        counts[status]=counts.get(status,0)+1
        plans.append(dict(symbol=symbol,signal_day=day,registered_at=stamp,status=status,details=json.loads(state)))
    result=dict(checked_at=now.isoformat(),scope='FORWARD_EOD_RESEARCH_NOT_LIVE_EXECUTION',
        portfolio_simulation=False,forward_validation_pass=False,real_orders_sent=0,
        counts=counts,plans=plans)
    atomic_status(Path(root)/'runtime'/'paper_status.json',result)
    return result


def run(root,now=None):
    root=Path(root);now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('Timezone-aware clock required')
    try:
        raw,_=read_history(root,today=now.astimezone(IST).date())
        added,registration=register(root,raw,now)
        try:calendars=json.loads((root/'runtime'/'calendar_history.json').read_text())
        except (OSError,ValueError):calendars={}
        reconcile(root,raw,calendars,now)
        result=report(root,now)
        result.update(new_registered=added,registration=registration,data_status='STRUCTURE_AND_ATTESTATION_VALID')
    except (OSError,ValueError,TypeError):
        result=report(root,now)
        result.update(data_status='WAITING_FOR_VALID_ADJUSTED_HISTORY',new_registered=0)
    atomic_status(root/'runtime'/'paper_status.json',result)
    return result
