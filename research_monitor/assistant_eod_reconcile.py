"""Reconcile approved paper holdings using ONLY completed adjusted NSE EOD bars.

Bar extrema cannot be replayed on entry day because those highs/lows could
occurred before the owner's YES. For later sessions this applies conservative
stop-first fills, next-day trailing stops, and a 100-session time limit.
The market calendar MUST account for every intervening calendar day.
No market-data fabrication, SMS orders or Groww order endpoints.
"""
from __future__ import annotations

from datetime import datetime,timedelta,timezone
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from research_monitor.assistant_trade_queue import ApprovalQueue
from research_monitor.history import read_history
from research_monitor.paper import expected_sessions
from research_monitor.paths import data_root
from research_monitor.schedule import IST,atomic_status
from swing_backtest import features

SCOPE="VERIFIED_COMPLETED_NSE_EOD_PAPER_HOLDING_RECONCILIATION"
MAX_POSITIONS=50


def reconcile_paper_eod(root,now=None,*,history_loader=read_history,
                        feature_builder=features):
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    runtime=root/"runtime"
    path=runtime/"assistant_trade_queue.sqlite3"
    summary={
        "scope":SCOPE,
        "updated_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"BLOCKED_SOURCES_UNVERIFIED",
        "open_paper_positions_examined":0,
        "completed_sessions_recorded":0,
        "paper_exits":0,
        "blocked_positions":0,
        "broker_protective_orders":"NOT_CONFIGURED",
        "real_orders":"DISABLED",
        "new_trade_approvals":0,
    }
    if not path.is_file():
        summary["state"]="NO_APPROVED_PAPER_HOLDINGS"
        atomic_status(runtime/"assistant_eod_status.json",summary)
        return summary
    try:
        raw,digest=history_loader(
            root,today=now.astimezone(IST).date()
        )
        calendar=json.loads(
            (runtime/"calendar_history.json").read_text(encoding="utf-8")
        )
        if not isinstance(calendar,dict):
            raise ValueError("VERIFIED_EXCHANGE_CALENDAR_REQUIRED")
        book=ApprovalQueue(path)
        holdings=[
            p for p in book.list_recent(limit=MAX_POSITIONS)
            if p["state"]=="OPEN_PAPER_ONLY"
        ]
        summary["open_paper_positions_examined"]=len(holdings)
        for holding in holdings:
            try:
                day=holding["last_session"] or holding["entered_session"]
                if not day:
                    raise ValueError("ENTRY_SESSION_UNRECORDED")
                last_day=now.astimezone(IST).date()-timedelta(days=1)
                if last_day<=pd.Timestamp(day).date():
                    continue
                sessions=expected_sessions(
                    calendar,day,last_day,now
                )
                expected=[session for session,_opening in sessions]
                company=holding["plan"]["symbol"]
                bars=raw.loc[raw.Symbol.eq(company)].sort_values("Date")
                completed=bars.loc[
                    (bars.Date>pd.Timestamp(day))
                    & (bars.Date<=pd.Timestamp(last_day))
                ].reset_index(drop=True)
                dates=[
                    str(stamp.date()) for stamp in completed.Date
                ]
                if dates!=expected:
                    raise ValueError("EOD_BARS_NOT_EXACTLY_VERIFIED_SESSIONS")
                if not expected:
                    continue
                # Recompute indicators using full historical prefix for each
                # bar; features are causal and do not use future closes.
                full=feature_builder(bars.reset_index(drop=True))
                keyed=full.set_index("Date")
                for date in expected:
                    bar=keyed.loc[pd.Timestamp(date)]
                    if isinstance(bar,pd.DataFrame):
                        raise ValueError("DUPLICATE_OHLCV_SESSION")
                    atr=float(bar["ATR"])
                    if not math.isfinite(atr) or atr<=0:
                        raise ValueError("INVALID_COMPLETED_EOD_ATR")
                    item=book.complete_eod_session(
                        holding["id"],date,{
                            name:float(bar[name])
                            for name in ("Open","High","Low","Close")
                        },atr,source_verified=True,
                    )
                    summary["completed_sessions_recorded"]+=1
                    if item["state"]=="CLOSED_PAPER_ONLY":
                        summary["paper_exits"]+=1
                        break
            except (OSError,ValueError,TypeError,KeyError,IndexError):
                summary["blocked_positions"]+=1
        summary["state"]=(
            "PAPER_EOD_RECONCILIATION_WITH_GAPS"
            if summary["blocked_positions"]
            else "PAPER_EOD_RECONCILIATION_COMPLETE"
        )
        summary["adjusted_ohlcv_source_sha"]=digest
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        summary["state"]="BLOCKED_SOURCES_UNVERIFIED"
    atomic_status(runtime/"assistant_eod_status.json",summary)
    return summary
