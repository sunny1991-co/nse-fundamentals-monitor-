"""Local, durable paper rehearsal only; no broker or network operations."""
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

BROKER_PREFERENCE = ('GROWW', 'UPSTOX', 'ANGEL_ONE')


def number(value):
    if isinstance(value, bool):
        raise ValueError('A finite positive number is required')
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('A finite positive number is required')
    return value


class PaperApprovals:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, plan TEXT NOT NULL,
                    state TEXT NOT NULL, expires REAL NOT NULL,
                    last_tick REAL, entry REAL, exit REAL, reason TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id TEXT NOT NULL, at REAL NOT NULL,
                    event TEXT NOT NULL, details TEXT NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def event(db, pid, now, event, details):
        db.execute('INSERT INTO events(proposal_id,at,event,details) VALUES(?,?,?,?)',
                   (pid, now, event, json.dumps(details, sort_keys=True)))

    def create(self, *, symbol, strategy, broker, capital, entry_min, entry_max,
               stop, target, fee_fraction, now=None, ttl=300):
        now = number(time.time() if now is None else now)
        ttl = number(ttl)
        if ttl > 900:
            raise ValueError('Paper approval expiry must be <= 900 seconds')
        if broker not in BROKER_PREFERENCE:
            raise ValueError('Unknown broker preference')
        if not re.fullmatch(r'[A-Z0-9&._-]{1,40}', symbol):
            raise ValueError('Invalid NSE symbol')
        if not re.fullmatch(r'[a-zA-Z0-9._-]{1,80}', strategy):
            raise ValueError('A strategy version is required')
        capital, low, high, stop, target = map(number,
            (capital, entry_min, entry_max, stop, target))
        fee = number(fee_fraction)
        if not 0 < fee < .05 or not stop < low <= high < target:
            raise ValueError('Invalid cost or long-equity price plan')
        unit_risk = high - stop + fee * (high + stop)
        qty = math.floor(min(capital * .01 / unit_risk,
                             capital * .25 / (high * (1 + fee))))
        if qty < 1:
            raise ValueError('Capital insufficient for one share within paper limits')
        plan = dict(symbol=symbol, strategy=strategy, broker=broker,
                    account='PAPER_ONLY', capital_inr=capital, entry_min=low,
                    entry_max=high, stop_loss=stop, target=target, quantity=qty,
                    estimated_cost_fraction_per_side=fee,
                    planned_risk_inr=round(qty * unit_risk, 2),
                    mode='PAPER_REHEARSAL', live_eligible=False,
                    cost_note='User estimate, not broker tariff; gaps can exceed planned risk',
                    portfolio_note='Independent proposal; no shared portfolio allocation')
        payload = json.dumps(plan, sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        pid = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO proposals(id,digest,plan,state,expires) VALUES(?,?,?,?,?)',
                       (pid, digest, payload, 'PENDING', now + ttl))
            self.event(db, pid, now, 'CREATED', {'digest': digest})
        return self.get(pid)

    @staticmethod
    def row(db, pid):
        row = db.execute('SELECT * FROM proposals WHERE id=?', (pid,)).fetchone()
        if row is None:
            raise ValueError('Unknown proposal')
        if hashlib.sha256(row['plan'].encode()).hexdigest() != row['digest']:
            raise ValueError('Proposal integrity check failed')
        return dict(row)

    def get(self, pid):
        with self.db() as db:
            row = self.row(db, pid)
        row['plan'] = json.loads(row['plan'])
        row['live_orders'] = 'DISABLED_NO_EXECUTION_ADAPTER'
        if row['exit'] is not None:
            p = row['plan']
            row['estimated_net_pnl_inr'] = round(p['quantity'] * (
                row['exit'] - row['entry'] - p['estimated_cost_fraction_per_side'] *
                (row['entry'] + row['exit'])), 2)
        return row

    def decide(self, pid, digest, answer, now=None):
        now = number(time.time() if now is None else now)
        if answer not in ('yes', 'no'):
            raise ValueError('Answer must be yes or no')
        with self.db() as db:
            row = self.row(db, pid)
            if row['digest'] != digest:
                raise ValueError('Approval does not match reviewed plan')
            if row['state'] != 'PENDING':
                raise ValueError('Proposal already decided; cannot approve twice')
            created = db.execute('SELECT MIN(at) FROM events WHERE proposal_id=?', (pid,)).fetchone()[0]
            if now < created:
                raise ValueError('Clock precedes proposal creation')
            state = 'EXPIRED' if now >= row['expires'] else (
                'APPROVED_PAPER' if answer == 'yes' else 'REJECTED')
            db.execute('UPDATE proposals SET state=? WHERE id=?', (state, pid))
            self.event(db, pid, now, state, {})
        return self.get(pid)

    def observe(self, pid, price, observed_at, now=None):
        """Replay an explicit price, not a verified feed. Stop gaps use observed price.

        Single prices cannot reconstruct candles or fills during disconnection.
        Approval expiry gates entry only; an open paper position can still exit.
        """
        now = number(time.time() if now is None else now)
        price, observed_at = number(price), number(observed_at)
        if not 0 <= now - observed_at <= 15:
            raise ValueError('Stale or future observation')
        with self.db() as db:
            row = self.row(db, pid)
            if row['state'] not in ('APPROVED_PAPER', 'OPEN_PAPER'):
                raise ValueError('Paper proposal is not approved or open')
            approved = db.execute("SELECT at FROM events WHERE proposal_id=? AND event='APPROVED_PAPER'", (pid,)).fetchone()
            if approved is None or observed_at < approved[0]:
                raise ValueError('Observation precedes approval')
            if row['last_tick'] is not None and observed_at <= row['last_tick']:
                raise ValueError('Duplicate or out-of-order observation')
            plan = json.loads(row['plan'])
            state, entry, exit_price, reason = row['state'], row['entry'], None, None
            if state == 'APPROVED_PAPER':
                if now >= row['expires']:
                    state = 'EXPIRED'
                elif plan['entry_min'] <= price <= plan['entry_max']:
                    state, entry = 'OPEN_PAPER', price
            elif price <= plan['stop_loss'] or price >= plan['target']:
                state, exit_price = 'CLOSED_PAPER', price
                reason = 'STOP' if price <= plan['stop_loss'] else 'TARGET'
            db.execute('''UPDATE proposals SET state=?,last_tick=?,entry=?,exit=?,reason=?
                          WHERE id=?''', (state, observed_at, entry, exit_price, reason, pid))
            self.event(db, pid, now, 'OBSERVATION',
                       dict(price=price, observed_at=observed_at, state=state, reason=reason))
        return self.get(pid)
