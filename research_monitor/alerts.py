"""Prepare an expiring, deduplicated notification outbox. Does not send WhatsApp.
Paper automation and real-money manual decisions remain separate.
"""
import hashlib
import json
import sqlite3
from datetime import datetime,timezone
from pathlib import Path
from contextlib import closing

EVENTS={'ENTRY_SETUP','PAPER_EXIT','PAPER_TRAILING_STOP','HOLDING_EXIT','HOLDING_TRAILING_STOP'}
REQUIRED={'instrument','strategy_version','reason','entry_min','entry_max','stop_loss',
          'targets','risk_note','data_as_of','expires_at','paper_summary','backtest_summary'}

def prepare_alert(path,event,now=None):
    now=now or datetime.now(timezone.utc)
    blockers=[]
    if event.get('module') not in {'SWING','FNO'}:
        blockers.append('MONITOR_MODULE_REQUIRED')
    if event.get('type') not in EVENTS or not event.get('event_id'):
        blockers.append('EVENT_ID_OR_TYPE_INVALID')
    if REQUIRED-set(event):
        blockers.append('ALERT_DETAILS_INCOMPLETE')
    try:
        stamp=datetime.fromisoformat(event['data_as_of'])
        expiry=datetime.fromisoformat(event['expires_at'])
        if stamp.tzinfo is None or expiry.tzinfo is None or not 0<=(now-stamp).total_seconds()<=15 or expiry<=now:
            blockers.append('STALE_OR_EXPIRED_ALERT')
    except (KeyError,TypeError,ValueError):
        blockers.append('TIMESTAMP_INVALID')
    if event.get('type')=='ENTRY_SETUP':
        for gate in ['backtest_validated','forward_paper_validated','current_setup_validated','risk_checks_passed']:
            if event.get(gate) is not True:
                blockers.append('MISSING_'+gate.upper())
    if event.get('type','').startswith('HOLDING_') and not (event.get('user_recorded_position_id') and event.get('position_reconciled') is True):
        blockers.append('REAL_HOLDING_NOT_RECONCILED')
    if blockers:
        return {'status':'BLOCKED','reasons':sorted(set(blockers))}
    # Identity is the originating strategy event, not the repeatedly polled price.
    identity=hashlib.sha256((event['module']+'|'+event['strategy_version']+'|'+event['event_id']).encode()).hexdigest()
    payload=dict(event)
    payload['execution']='USER_MANUAL_IN_GROWW_NO_ORDER_SENT'
    payload['position_scope']='USER_RECORDED_HOLDING' if event['type'].startswith('HOLDING_') else 'PAPER_RESEARCH'
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path,timeout=10)) as db:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY,created_at TEXT,expires_at TEXT,payload TEXT,status TEXT)')
            added=db.execute('INSERT OR IGNORE INTO outbox VALUES(?,?,?,?,?)',
                (identity,now.isoformat(),expiry.isoformat(),json.dumps(payload), 'PENDING_CHANNEL_SETUP')).rowcount
    Path(path).chmod(0o600)
    return {'status':'QUEUED_NOT_SENT' if added else 'DUPLICATE_SUPPRESSED','id':identity}
