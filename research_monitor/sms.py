"""MSG91 approved-template transport. No credentials or phone numbers in logs."""
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
import requests

ENDPOINT = 'https://control.msg91.com/api/v5/flow'

def deliver(path, alert_id, env=None, now=None, post=None):
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    post = post or requests.post
    required = ['MSG91_AUTH_KEY', 'SMS_TEMPLATE_ID', 'SMS_RECIPIENT']
    if env.get('SMS_ENABLED') != '1' or env.get('SMS_TEMPLATE_APPROVED') != '1' or any(not env.get(k) for k in required):
        return 'PROVIDER_SETUP_REQUIRED'
    if not re.fullmatch(r'91[6-9]\d{9}', env['SMS_RECIPIENT']):
        return 'RECIPIENT_INVALID'
    # Claim before network I/O. Uncertain delivery must be reconciled with provider,
    # never automatically resent (which could duplicate an actionable alert).
    with sqlite3.connect(path, timeout=10) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT payload,status,expires_at FROM outbox WHERE id=?', (alert_id,)).fetchone()
        if not row or row[1] != 'PENDING_CHANNEL_SETUP':
            return 'NOT_PENDING'
        event = json.loads(row[0])
        stamp = datetime.fromisoformat(event['data_as_of'])
        expiry = datetime.fromisoformat(row[2])
        if expiry <= now or not 0 <= (now-stamp).total_seconds() <= 15:
            db.execute('UPDATE outbox SET status=? WHERE id=?', ('EXPIRED', alert_id))
            return 'EXPIRED'
        if event.get('module') != 'SWING':
            return 'SCOPE_BLOCKED'
        # Template must contain these exact placeholders. Full evidence stays in app.
        recipient = dict(mobiles=env['SMS_RECIPIENT'], symbol=str(event['instrument']),
            event=str(event['type']), entry=f"{event['entry_min']}-{event['entry_max']}",
            stop=str(event['stop_loss']), targets='/'.join(map(str,event['targets'])),
            expiry=event['expires_at'], scope=event['position_scope'])
        if any(len(v)>40 for k,v in recipient.items() if k!='mobiles'):
            return 'TEMPLATE_VALUE_TOO_LONG'
        db.execute('UPDATE outbox SET status=? WHERE id=?', ('SENDING', alert_id))
    try:
        response = post(ENDPOINT, headers={'authkey':env['MSG91_AUTH_KEY'], 'accept':'application/json'},
            json={'template_id':env['SMS_TEMPLATE_ID'], 'short_url':'0', 'recipients':[recipient]}, timeout=10)
        body = response.json()
        outcome = 'ACCEPTED_BY_PROVIDER' if response.ok and isinstance(body,dict) and body.get('type')=='success' else 'PROVIDER_REJECTED'
    except (requests.RequestException, ValueError):
        outcome = 'DELIVERY_UNKNOWN_DO_NOT_RETRY'
    with sqlite3.connect(path, timeout=10) as db:
        db.execute('UPDATE outbox SET status=? WHERE id=?', (outcome,alert_id))
    return outcome
