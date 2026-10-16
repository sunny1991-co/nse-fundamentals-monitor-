"""One-question human approval for strategy-selected NSE EQ swings (PAPER ONLY).

The assistant may evaluate market data and propose *hypothetical* entries.
Only the owner may answer YES plus an explicit integer quantity, or NO.
Approval is bound to the exact price plan and expires in 5 minutes.
The research-only position then reacts to fresh observations with simulated
stops, 2R partial profits, 3R exits and 100-session maximum holding.
NEVER calls a broker, modifies market data, sends a live order, or claims a
verified price feed. Live mode is deliberately not implemented.

Use this as the durable state machine behind an authenticated dashboard.
Actual current NSE fundamentals and independent prospective strategy
validation still block actionable live trading.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time

MODE="PAPER_RESEARCH_ONLY"
STRATEGY="quality-leader-contraction-breakout-v1"
MAX_RISK_FRACTION=.01
MAX_POSITION_FRACTION=.25
MAX_APPROVAL_TTL_SECONDS=300
MAX_QUOTE_AGE_SECONDS=15
MAX_HOLD_SESSIONS=100


def finite(value):
    if isinstance(value,bool):
        raise ValueError("BOOLEAN_NOT_A_FINANCIAL_VALUE")
    try:
        n=float(value)
    except (ValueError,TypeError,OverflowError):
        raise ValueError("NONFINITE_NUMBER") from None
    if not math.isfinite(n):
        raise ValueError("NONFINITE_NUMBER")
    return n


def safe_quantity(value):
    if isinstance(value,bool) or not isinstance(value,(int,str)):
        raise ValueError("INTEGER_QUANTITY_REQUIRED")
    raw=str(value)
    if not re.fullmatch(r"[1-9][0-9]{0,6}",raw):
        raise ValueError("INTEGER_QUANTITY_REQUIRED")
    return int(raw)


def _digest(payload):
    return hashlib.sha256(
        json.dumps(payload,sort_keys=True,separators=(",",":")).encode()
    ).hexdigest()


class ApprovalQueue:
    """Transactional SQLite store: duplicate approval/fill is not replayed."""
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                signal_identity TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL,
                plan TEXT NOT NULL,
                state TEXT NOT NULL,
                expires REAL NOT NULL,
                quantity INTEGER,
                last_observed_at REAL,
                entry REAL,
                shares_remaining INTEGER,
                shares_sold INTEGER NOT NULL DEFAULT 0,
                realized_cash REAL NOT NULL DEFAULT 0,
                stop_current REAL,
                t1_done INTEGER NOT NULL DEFAULT 0,
                entered_session TEXT,
                completed_session_count INTEGER NOT NULL DEFAULT 0,
                last_session TEXT,
                exit REAL,
                exit_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS audit (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL,
                stamp REAL NOT NULL,
                event TEXT NOT NULL,
                fields TEXT NOT NULL
            );""")
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        conn=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        conn.row_factory=sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _event(conn,pid,stamp,name,fields=None):
        conn.execute("INSERT INTO audit(id,stamp,event,fields) VALUES(?,?,?,?)",
                     (pid,stamp,name,json.dumps(fields or {},sort_keys=True)))

    @staticmethod
    def _row(conn,pid):
        row=conn.execute("SELECT * FROM trades WHERE id=?",(pid,)).fetchone()
        if row is None:
            raise ValueError("UNKNOWN_TRADE_PROPOSAL")
        r=dict(row)
        plan=json.loads(r["plan"])
        if _digest(plan)!=r["fingerprint"]:
            raise ValueError("TRADE_PLAN_TAMPERED")
        r["plan"]=plan
        return r

    @staticmethod
    def _public(row):
        record={k:v for k,v in row.items() if k not in ("plan",)}
        record["plan"]=row["plan"]
        record["paper_only"]=True
        record["real_orders"]="DISABLED_NO_VERIFIED_EXECUTION_ADAPTER"
        record["broker_preference"]="GROWW"
        record["historically_calibrated_win_probability"]=None
        record["live_trade_authorized"]=False
        return record

    def list_recent(self,limit=20):
        if not isinstance(limit,int) or not 1<=limit<=50:
            raise ValueError("LIMIT_OUT_OF_RANGE")
        with self.db() as conn:
            ids=[x[0] for x in conn.execute(
                "SELECT id FROM trades ORDER BY rowid DESC LIMIT ?",(limit,)
            )]
            return [self._public(self._row(conn,pid)) for pid in ids]

    def get(self,pid):
        with self.db() as conn:
            return self._public(self._row(conn,pid))

    def propose(self, *, symbol,signal_day,entry_min,entry_max,stop,
                target_1,target_2,capital_inr,source_sha,
                signal_price,now=None,ttl=MAX_APPROVAL_TTL_SECONDS,
                fee_fraction_per_side=.003):
        now=finite(time.time() if now is None else now)
        ttl=finite(ttl)
        if not 0<ttl<=MAX_APPROVAL_TTL_SECONDS:
            raise ValueError("APPROVAL_TTL_OUT_OF_RANGE")
        if not re.fullmatch(r"[A-Z0-9&._-]{1,40}",str(symbol)):
            raise ValueError("NSE_EQ_SYMBOL_REQUIRED")
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}",str(signal_day)):
            raise ValueError("SIGNAL_DAY_REQUIRED")
        # Enforces reasonable chronology, but NOT a claim of verified NSE feed.
        ts=datetime.strptime(signal_day,"%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        if ts>now or now-ts>4*86400:
            raise ValueError("STALE_OR_FUTURE_SIGNAL_DAY")
        if not re.fullmatch(r"[a-f0-9]{64}",str(source_sha)):
            raise ValueError("SOURCE_FINGERPRINT_REQUIRED")
        low,high,s,one,two,capital,price,fee=map(
            finite,(entry_min,entry_max,stop,target_1,target_2,capital_inr,
                    signal_price,fee_fraction_per_side)
        )
        if (not 0<s<low<=high<one<two or capital<=0
                or not 0<fee<.05 or not low<=price<=high):
            raise ValueError("INVALID_ENTRY_ZONE_OR_PRICED_OBSERVATION")
        unit_risk=high-s+fee*(high+s)
        max_qty=min(
            math.floor(capital*MAX_RISK_FRACTION/unit_risk),
            math.floor(capital*MAX_POSITION_FRACTION/(high*(1+fee)))
        )
        if max_qty<1 or max_qty>1_000_000:
            raise ValueError("NO_SAFE_INTEGER_QUANTITY")
        # Broker is *not* included in signal identity because the queue
        # currently cannot switch brokers or trade on any broker.
        identity=_digest({
            "strategy":STRATEGY,"symbol":symbol,
            "signal_day":signal_day,"source_sha":source_sha,
        })
        plan={
            "symbol":symbol,"exchange":"NSE","series":"EQ",
            "strategy":STRATEGY,"signal_day":signal_day,
            "entry_min":low,"entry_max":high,
            "stop_loss":s,"target_1":one,"target_2":two,
            "capital_inr":capital,"fee_fraction_per_side":fee,
            "maximum_approved_quantity":max_qty,
            "max_initial_stop_risk_inr":round(max_qty*unit_risk,2),
            "max_holding_sessions":MAX_HOLD_SESSIONS,
            "source_sha256":source_sha,
            "approval_covers_exact_price_plan_only":True,
            "fundamentals_independently_verified":False,
            "strategy_profitable_proven":False,
            "quote_source_independently_verified":False,
            "mode":MODE,
            "orders":"DISABLED",
        }
        digest=_digest(plan)
        with self.db() as conn:
            existing=conn.execute(
                "SELECT id FROM trades WHERE signal_identity=?",(identity,)
            ).fetchone()
            if existing:
                # Same signal cannot be recreated with changed entry/stop,
                # reset/extended expiry or duplicate a rejected proposal.
                row=self._row(conn,existing[0])
                if row["fingerprint"]!=digest:
                    raise ValueError("SIGNAL_HAS_CONFLICTING_PRICE_PLAN")
                return self._public(row)
            pid=secrets.token_hex(16)
            conn.execute("""INSERT INTO trades
                (id,signal_identity,fingerprint,plan,state,expires)
                VALUES(?,?,?,?,?,?)""",(
                pid,identity,digest,json.dumps(
                    plan,sort_keys=True,separators=(",",":")
                ),"WAITING_FOR_OWNER",now+ttl,
            ))
            self._event(conn,pid,now,"RESEARCH_PLAN_PROPOSED",{
                "mode":MODE,"max_quantity":max_qty
            })
            return self._public(self._row(conn,pid))

    def decide(self,pid,fingerprint,answer,quantity=None,now=None):
        now=finite(time.time() if now is None else now)
        if answer not in ("yes","no"):
            raise ValueError("YES_OR_NO_REQUIRED")
        with self.db() as conn:
            row=self._row(conn,pid)
            if row["fingerprint"]!=fingerprint:
                raise ValueError("EXACT_PLAN_FINGERPRINT_CHANGED")
            if row["state"]!="WAITING_FOR_OWNER":
                raise ValueError("APPROVAL_ALREADY_DECIDED")
            created=conn.execute(
                "SELECT MIN(stamp) FROM audit WHERE id=?",(pid,)
            ).fetchone()[0]
            if now<created:
                raise ValueError("DECISION_CLOCK_BEFORE_PROPOSAL")
            if now>=row["expires"]:
                state,qty="EXPIRED",None
            elif answer=="no":
                state,qty="REJECTED",None
            else:
                qty=safe_quantity(quantity)
                if qty>row["plan"]["maximum_approved_quantity"]:
                    raise ValueError("QUANTITY_EXCEEDS_RISK_OR_POSITION_LIMIT")
                state="ARMED_PAPER_ONLY"
            conn.execute(
                "UPDATE trades SET state=?,quantity=? WHERE id=?",
                (state,qty,pid)
            )
            self._event(conn,pid,now,state,{
                "quantity":qty,"user_choice":answer,
                "real_order_created":False,
            })
            return self._public(self._row(conn,pid))

    def observe(self,pid,price,observed_at,now=None,*,verified_feed=False,
                session_day=None,session_closed=False,atr=None):
        """Advance paper orders via trusted *research* observation only.

        Caller must verify symbol, market session, quote provenance, feed
        timestamp/sequence and price independently. A boole flag is NOT
        itself a market data entitlement. This method has no broker I/O.
        """
        now=finite(time.time() if now is None else now)
        price=finite(price)
        observed_at=finite(observed_at)
        if price<=0 or verified_feed is not True:
            raise ValueError("FRESH_SOURCE_VERIFICATION_REQUIRED")
        if not 0<=now-observed_at<=MAX_QUOTE_AGE_SECONDS:
            raise ValueError("STALE_OR_FUTURE_QUOTE")
        if session_day is not None:
            if not isinstance(session_day,str) or not re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}",session_day
            ):
                raise ValueError("VERIFIED_SESSION_DAY_REQUIRED")
        with self.db() as conn:
            row=self._row(conn,pid)
            if row["state"] not in ("ARMED_PAPER_ONLY","OPEN_PAPER_ONLY"):
                raise ValueError("NOT_ARMED_OR_OPEN")
            approved=conn.execute(
                "SELECT stamp FROM audit WHERE id=? AND event='ARMED_PAPER_ONLY'",
                (pid,)
            ).fetchone()
            if approved is None or observed_at<approved[0]:
                raise ValueError("QUOTE_BEFORE_APPROVAL")
            if row["last_observed_at"] is not None and observed_at<=row["last_observed_at"]:
                raise ValueError("DUPLICATE_OR_REVERSED_FEED_TICK")
            plan=row["plan"]
            state=row["state"]
            updates={"last_observed_at":observed_at}
            event="PAPER_PRICE_OBSERVED"
            if state=="ARMED_PAPER_ONLY":
                if now>=row["expires"]:
                    state="EXPIRED"
                elif plan["entry_min"]<=price<=plan["entry_max"]:
                    state="OPEN_PAPER_ONLY"
                    updates.update(
                        entry=price,shares_remaining=row["quantity"],
                        shares_sold=0,realized_cash=0.,
                        stop_current=plan["stop_loss"],
                        entered_session=session_day,
                        completed_session_count=0,
                    )
                    event="PAPER_ENTRY_MATCHED_WITHIN_ZONE"
            else:
                stop=float(row["stop_current"])
                remaining=int(row["shares_remaining"])
                sold=int(row["shares_sold"])
                cash=float(row["realized_cash"])
                # Stop priority if a quote is at/below stop; gaps fill
                # at the observed price, never at a fictitious stop value.
                if price<=stop:
                    cash+=remaining*price
                    sold+=remaining
                    updates.update(shares_sold=sold,shares_remaining=0,
                                   realized_cash=cash,exit=price,
                                   exit_reason="STOP_OR_GAP")
                    state="CLOSED_PAPER_ONLY"
                    event="PAPER_STOP_EXIT"
                elif price>=plan["target_2"]:
                    cash+=remaining*price
                    sold+=remaining
                    updates.update(shares_sold=sold,shares_remaining=0,
                                   realized_cash=cash,exit=price,
                                   exit_reason="TARGET_3R")
                    state="CLOSED_PAPER_ONLY"
                    event="PAPER_TARGET_EXIT"
                elif not row["t1_done"] and price>=plan["target_1"]:
                    to_sell=remaining//2 if remaining>=2 else remaining
                    cash+=to_sell*price
                    left=remaining-to_sell
                    updates.update(
                        shares_sold=sold+to_sell,shares_remaining=left,
                        realized_cash=cash,t1_done=1,
                        stop_current=max(stop,float(row["entry"])),
                    )
                    event="PAPER_TARGET_2R_PARTIAL"
                    if left==0:
                        state="CLOSED_PAPER_ONLY"
                        updates.update(exit=price,exit_reason="TARGET_2R_SINGLE_SHARE")
                # Trailing only on a completed validated session, not on
                # speculative intraday ATR updates or current tick.
                if state=="OPEN_PAPER_ONLY" and session_closed:
                    if session_day is None or atr is None:
                        raise ValueError("COMPLETED_SESSION_ATR_REQUIRED")
                    atr_value=finite(atr)
                    if atr_value<=0:
                        raise ValueError("ATR_REQUIRED")
                    if row["last_session"] and session_day<=row["last_session"]:
                        raise ValueError("SESSION_BAR_OUT_OF_ORDER")
                    sessions=int(row["completed_session_count"])+1
                    updates.update(last_session=session_day,
                                   completed_session_count=sessions)
                    if row["t1_done"] or updates.get("t1_done")==1:
                        updates["stop_current"]=max(
                            float(updates.get("stop_current",stop)),
                            float(row["entry"]),
                            price-2*atr_value,
                        )
                    if sessions>=MAX_HOLD_SESSIONS:
                        cash=float(updates.get("realized_cash",cash))
                        left=int(updates.get("shares_remaining",remaining))
                        cash+=left*price
                        updates.update(
                            shares_remaining=0,shares_sold=sold+left,
                            realized_cash=cash,exit=price,exit_reason="TIME_100_SESSIONS"
                        )
                        state="CLOSED_PAPER_ONLY"
                        event="PAPER_TIME_EXIT"
            updates["state"]=state
            assignments=",".join(f"{key}=?" for key in updates)
            conn.execute(
                f"UPDATE trades SET {assignments} WHERE id=?",
                [*updates.values(),pid],
            )
            self._event(conn,pid,now,event,{
                "paper_only":True,"price":price,"status":state,
                "quantity_remaining":updates.get("shares_remaining",
                                                  row["shares_remaining"]),
            })
            return self._public(self._row(conn,pid))
