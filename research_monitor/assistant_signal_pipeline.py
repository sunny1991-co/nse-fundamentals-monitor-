"""Strict EOD strategy research -> verified live quote -> PAPER approval queue.

There is deliberately NO broker order endpoint. Reading a market quote does
not authorize a trade. This bridge is designed to be supervised by the
existing market worker once all strict source, date and quote gates pass.

Signals on a new open session depend only on the prior *completed* daily bar.
The benchmark is NIFTY 50 TOTAL RETURN, never a substitute price-only NIFTY.
Vendor-only financials cannot qualify a stock for this strict shortlist.
"""
from __future__ import annotations

from datetime import datetime,timezone,timedelta
import csv
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from research_monitor.assistant_trade_queue import ApprovalQueue
from research_monitor.history import read_history
from research_monitor.paths import data_root
from research_monitor.quality_leader_rules import (
    STRATEGY_ID,PARAMETERS,quality_leader_features,
)
from research_monitor.schedule import IST,atomic_status
from research_monitor.strategy_sources import read_nifty50_tri
from research_monitor.risk import quote_errors

SCOPE="EXPLAINABLE_EOD_SWING_ASSISTANT_PAPER_RESEARCH_V1"
QUOTE_SCOPE="PAPER_ONLY_AFTER_EXACT_OWNER_YES_AND_INTEGER_QUANTITY"
MAX_WATCHLIST=15
MAX_QUOTE_AGE=15
RESEARCH_CAPITAL=100_000.0


def _hash_file(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("SOURCE_FILE_MISSING_OR_SYMLINK")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fresh_fundamental_universe(root,now):
    shortlist=root/"final_fundamental_shortlist.csv"
    summary=root/"final_fundamental_screen_summary.csv"
    with shortlist.open(encoding="utf-8-sig",newline="") as file:
        reader=csv.DictReader(file)
        needed={"Symbol","Final_Fundamental_Criteria_Pass"}
        if not reader.fieldnames or not needed.issubset(reader.fieldnames):
            raise ValueError("STRICT_FUNDAMENTAL_SHORTLIST_SCHEMA_MISSING")
        rows=list(reader)
    with summary.open(encoding="utf-8-sig",newline="") as file:
        report=next(csv.DictReader(file),{})
    day=datetime.strptime(report["Report_Generated_On"][:10],"%Y-%m-%d").date()
    today=now.astimezone(IST).date()
    if (today-day).days not in (0,1):
        raise ValueError("STRICT_FUNDAMENTAL_SHORTLIST_STALE")
    verified={
        str(r["Symbol"]).upper() for r in rows
        if r["Final_Fundamental_Criteria_Pass"].strip().lower()=="true"
    }
    if len(verified)!=sum(
        r["Final_Fundamental_Criteria_Pass"].strip().lower()=="true"
        for r in rows
    ):
        raise ValueError("DUPLICATE_QUALIFIED_FUNDAMENTAL_SYMBOL")
    if len(verified)!=int(report["Final_Shortlist_Count"]):
        raise ValueError("FUNDAMENTAL_SUMMARY_AND_SHORTLIST_DISAGREE")
    return verified, (_hash_file(shortlist),_hash_file(summary))


def _report(root,result,*,delete_watchlist=False):
    runtime=root/"runtime"
    runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
    if delete_watchlist:
        (runtime/"assistant_research_watchlist.json").unlink(missing_ok=True)
    atomic_status(runtime/"assistant_scan_status.json",result)
    return result


def scan(root,now=None,*,history_loader=read_history,
         benchmark_loader=read_nifty50_tri,feature_builder=quality_leader_features):
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("AWARE_CLOCK_REQUIRED")
    report={
        "scope":SCOPE,
        "updated_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"BLOCKED_SOURCE_REQUIRED",
        "strategy":STRATEGY_ID,
        "watchlist_count":0,
        "fundamentals_source":"STRICT_FINAL_NSE_SCREEN_ONLY",
        "research_capital_inr_not_user_account_balance":RESEARCH_CAPITAL,
        "watchlist_is_live_recommendation":False,
        "owner_approval_needed_for_paper":True,
        "trade_orders":"DISABLED",
    }
    try:
        passed, fund_hashes=_fresh_fundamental_universe(root,now)
        if not passed:
            report["state"]="NO_FULL_STRICT_FUNDAMENTAL_PASS"
            return _report(root,report,delete_watchlist=True)
        history,digest=history_loader(root,today=now.astimezone(IST).date())
        benchmark,tri_digest=benchmark_loader(root)
        as_of=now.astimezone(IST).date()
        candidates=[]
        for symbol,bars in history.groupby("Symbol"):
            if symbol not in passed or len(bars)<1260:
                continue
            data=feature_builder(bars,benchmark)
            if data.empty:
                continue
            last=data.iloc[-1]
            signal_day=pd.Timestamp(last.Date).date()
            if (as_of-signal_day).days not in (1,2,3,4):
                continue
            if not bool(last.Signal):
                continue
            close=float(last.Close)
            stop=float(last.Stop)
            relative=float(last.Relative_TRI_63d_Return)
            if not all(math.isfinite(x) for x in (close,stop,relative)):
                continue
            if not 0<stop<close or not .02<=(close-stop)/close<=.10:
                continue
            risk=close-stop
            entry_high=close*(1+PARAMETERS[
                "max_next_session_entry_gap_above_signal_close"
            ])
            candidates.append({
                "symbol":symbol,
                "signal_day":signal_day.isoformat(),
                "entry_min":round(close,4),
                "entry_max":round(entry_high,4),
                "stop":round(stop,4),
                "target_1":round(close+2*risk,4),
                "target_2":round(close+3*risk,4),
                "relative_nifty_tri_63_session_return":round(relative,5),
                "source_as_of":signal_day.isoformat(),
                "scope":"RESEARCH_ONLY_NOT_VERIFIED_FORWARD_STRATEGY",
            })
        # Deterministic relative-strength priority is NOT a win rate.
        candidates.sort(key=lambda r:(
            -r["relative_nifty_tri_63_session_return"],r["symbol"]
        ))
        candidates=candidates[:MAX_WATCHLIST]
        # Digest ties the exact market OHLCV source, TRI and current strict
        # fundamentals to this immutable paper evaluation.
        # Bind BOTH data files and their provenance/adjustment attestations.
        # Replacing metadata alone must invalidate the pending paper proposal.
        meta_hashes=(
            _hash_file(root/"market_data_metadata.json"),
            _hash_file(root/"nifty50_tri_history_metadata.json"),
        )
        source_sha=hashlib.sha256(
            "|".join((digest,tri_digest,*fund_hashes,*meta_hashes)).encode()
        ).hexdigest()
        for item in candidates:
            item["source_sha"]=source_sha
        output={
            "scope":SCOPE,
            "generated_at_utc":now.astimezone(timezone.utc).isoformat(),
            "watchlist_day_ist":as_of.isoformat(),
            "source_sha":source_sha,
            "ohlcv_sha":digest,
            "tri_sha":tri_digest,
            "fundamental_shortlist_sha":fund_hashes[0],
            "fundamental_screen_summary_sha":fund_hashes[1],
            "ohlcv_metadata_sha":meta_hashes[0],
            "tri_metadata_sha":meta_hashes[1],
            "entries":candidates,
            "eligible_for_live_orders":False,
            "signals_are_prospective_validation":False,
            "orders":"DISABLED",
        }
        path=root/"runtime"/"assistant_research_watchlist.json"
        atomic_status(path,output)
        report["state"]="RESEARCH_WATCHLIST_READY_PAPER_ONLY"
        report["watchlist_count"]=len(candidates)
        report["current_verified_fundamental_symbols"]=len(passed)
        report["historical_ohlcv_digest"]=digest
        report["tri_digest"]=tri_digest
        return _report(root,report)
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        return _report(root,report,delete_watchlist=True)


def match_quote_and_prepare_paper_prompt(root,now=None):
    """Only matches an existing frozen watchlist to a current worker quote.

    No manual price entry from an unauthenticated route is possible. A
    broker-data quote STILL does not establish that any strategy is
    profitable, nor does it enable real execution after user approval.
    """
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("AWARE_CLOCK_REQUIRED")
    runtime=root/"runtime"
    path=runtime/"assistant_research_watchlist.json"
    try:
        stored=json.loads(path.read_text())
        worker=json.loads((runtime/"worker_status.json").read_text())
        quotes=json.loads((runtime/"quotes.json").read_text())
        if stored.get("scope")!=SCOPE or stored.get("eligible_for_live_orders") is not False:
            raise ValueError("WRONG_WATCHLIST_SCOPE")
        when=datetime.fromisoformat(stored["generated_at_utc"])
        if when.tzinfo is None or (now-when).total_seconds()<0 or (
            now-when).total_seconds()>5*86400:
            raise ValueError("STALE_WATCHLIST")
        if stored["watchlist_day_ist"]!=now.astimezone(IST).date().isoformat():
            raise ValueError("WATCHLIST_NOT_FOR_TODAY")
        if worker.get("phase")!="LIVE_MONITORING" or worker.get("live_feed")!="POLLING_10_SECONDS":
            raise ValueError("LIVE_QUOTE_WORKER_NOT_VERIFIED")
        worker_time=datetime.fromisoformat(worker["updated_at"])
        quote_time=datetime.fromisoformat(quotes["fetched_at"])
        if any(stamp.tzinfo is None or not 0<=(now-stamp).total_seconds()<=15
               for stamp in (worker_time,quote_time)):
            raise ValueError("WORKER_OR_QUOTES_STALE")
        expected_sha=hashlib.sha256("|".join((
            _hash_file(root/"market_ohlcv.csv"),
            _hash_file(root/"nifty50_tri_history.csv"),
            _hash_file(root/"final_fundamental_shortlist.csv"),
            _hash_file(root/"final_fundamental_screen_summary.csv"),
            _hash_file(root/"market_data_metadata.json"),
            _hash_file(root/"nifty50_tri_history_metadata.json"),
        )).encode()).hexdigest()
        # Original OHLCV digest SHA and TRI digest must be bound to source
        # files and accompanying attestation metadata; missing meta blocks.
        if expected_sha!=stored.get("source_sha"):
            raise ValueError("SOURCE_INPUTS_CHANGED")
        if not isinstance(stored["entries"],list) or len(stored["entries"])>MAX_WATCHLIST:
            raise ValueError("WATCHLIST_UNTRUSTED")
        quote_rows=quotes.get("quotes")
        if not isinstance(quote_rows,list):
            raise ValueError("QUOTE_LIST_REQUIRED")
        by_symbol={}
        for q in quote_rows:
            if not isinstance(q,dict):
                raise ValueError("QUOTE_LIST_UNVERIFIED")
            symbol=q.get("symbol")
            if symbol in by_symbol:
                raise ValueError("DUPLICATE_MARKET_QUOTE")
            if (
                q.get("exchange")!="NSE"
                or not str(q.get("token","")).startswith("NSE_EQ|")
                or q.get("validation_errors")!=[]
            ):
                raise ValueError("UNVERIFIED_NSE_EQ_MARKET_QUOTE")
            if quote_errors(
                q,{"max_quote_age_seconds":MAX_QUOTE_AGE,
                   "max_spread_fraction":.005},now
            ):
                raise ValueError("QUOTE_VALIDATION_FAILED")
            by_symbol[symbol]=q
        book=ApprovalQueue(
            runtime/"assistant_trade_queue.sqlite3"
        )
        new=[]
        for item in stored["entries"]:
            quote=by_symbol.get(item["symbol"])
            if not quote:
                continue
            # Model attempted buy fill at ask + 0.1% safety buffer,
            # reject spread/wide chase already at prompt stage.
            ask=float(quote["ask"])*1.001
            if not item["entry_min"]<=ask<=item["entry_max"]:
                continue
            created=book.propose(
                symbol=item["symbol"],
                signal_day=item["signal_day"],
                entry_min=item["entry_min"],
                entry_max=item["entry_max"],
                stop=item["stop"],
                target_1=item["target_1"],
                target_2=item["target_2"],
                capital_inr=RESEARCH_CAPITAL,
                source_sha=item["source_sha"],
                signal_price=ask,
                now=now.timestamp(),
            )
            if created["state"]=="WAITING_FOR_OWNER":
                new.append({
                    "id":created["id"],
                    "symbol":created["plan"]["symbol"],
                    "max_qty":created["plan"]["maximum_approved_quantity"],
                    "state":"WAITING_FOR_OWNER_PAPER_ONLY",
                })
        return {
            "scope":QUOTE_SCOPE,
            "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
            "owner_prompts":new,
            "live_orders":0,
            "strategy_validated":False,
            "real_broker_execution_enabled":False,
        }
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        return {
            "scope":QUOTE_SCOPE,
            "owner_prompts":[],
            "state":"SOURCE_QUOTE_OR_WATCHLIST_NOT_VERIFIED",
            "live_orders":0,
            "real_broker_execution_enabled":False,
        }



def advance_approved_paper_positions(root,now=None):
    """Automatically move pre-approved *paper* shares on verified quote ticks.

    Does not require a still-current watchlist: an existing paper holding
    must remain observable even when source screening is blocked. A stale
    feed does not fabricate an exit; the position is flagged unprotected
    in research, and real-money trading remains disabled.
    """
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    runtime=root/"runtime"
    db=runtime/"assistant_trade_queue.sqlite3"
    if not db.is_file():
        return {
            "state":"NO_PAPER_APPROVAL_LEDGER",
            "updated_positions":0,"orders":"DISABLED",
        }
    try:
        worker=json.loads((runtime/"worker_status.json").read_text())
        quotes=json.loads((runtime/"quotes.json").read_text())
        if worker.get("phase")!="LIVE_MONITORING" or (
            worker.get("live_feed")!="POLLING_10_SECONDS"
        ):
            raise ValueError("VERIFIED_LIVE_PAPER_FEED_NOT_AVAILABLE")
        stamps=[
            datetime.fromisoformat(worker["updated_at"]),
            datetime.fromisoformat(quotes["fetched_at"]),
        ]
        if any(stamp.tzinfo is None or not 0<=(now-stamp).total_seconds()<=15
               for stamp in stamps):
            raise ValueError("BROKER_READ_ONLY_QUOTE_FEED_STALE")
        observations={}
        for q in quotes.get("quotes",[]):
            if (
                not isinstance(q,dict)
                or q.get("exchange")!="NSE"
                or not str(q.get("token","")).startswith("NSE_EQ|")
                or q.get("validation_errors")!=[]
                or quote_errors(
                    q,{"max_quote_age_seconds":15,
                       "max_spread_fraction":.005},now
                )
            ):
                raise ValueError("PAPER_PRICE_VALIDATION_FAILED")
            sym=q.get("symbol")
            if sym in observations:
                raise ValueError("DUPLICATE_EQUITY_PRICE_OBSERVATION")
            timestamp=datetime.fromisoformat(str(q["feed_time"]))
            if timestamp.tzinfo is None:
                raise ValueError("QUOTE_TIMEZONE_NOT_PRESENT")
            observations[sym]=(timestamp.timestamp(),float(q["bid"]),
                               float(q["ask"]))
        book=ApprovalQueue(db)
        updated=[]
        for proposal in book.list_recent(limit=50):
            if proposal["state"] not in ("ARMED_PAPER_ONLY","OPEN_PAPER_ONLY"):
                continue
            quote=observations.get(proposal["plan"]["symbol"])
            if quote is None:
                continue
            stamp,bid,ask=quote
            # Simulate a worse-than-quote execution including 0.1% slippage:
            # purchase at the ask + slippage, exit at bid - slippage.
            modeled_price=(
                ask*1.001 if proposal["state"]=="ARMED_PAPER_ONLY"
                else bid*.999
            )
            try:
                after=book.observe(
                    proposal["id"],modeled_price,stamp,
                    now=now.timestamp(),verified_feed=True,
                    session_day=now.astimezone(IST).date().isoformat(),
                )
            except ValueError:
                # An uncertain or duplicate observation cannot result in
                # a second fill or silently close the paper position.
                continue
            if after["state"]!=proposal["state"]:
                updated.append({
                    "symbol":after["plan"]["symbol"],
                    "id":after["id"],
                    "from":proposal["state"],
                    "to":after["state"],
                    "paper_only":True,
                })
        return {
            "scope":QUOTE_SCOPE,
            "state":"PAPER_TICK_PROCESSING_COMPLETE",
            "updated_positions":len(updated),
            "transitions":updated,
            "orders":"DISABLED",
            "automated_broker_exits":"NOT_IMPLEMENTED",
        }
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        return {
            "scope":QUOTE_SCOPE,
            "state":"PAPER_QUOTES_MISSING_OR_STALE_NO_FILL_ASSUMED",
            "updated_positions":0,
            "orders":"DISABLED",
            "automated_broker_exits":"NOT_IMPLEMENTED",
        }
