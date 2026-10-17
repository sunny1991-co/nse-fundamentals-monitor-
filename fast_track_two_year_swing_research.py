#!/usr/bin/env python3
"""Bounded fast-track NSE swing EOD RESEARCH, never a trading source.

Uses the existing two-*independently attested* NSE FY2024-25/FY2025-26
provisional quality shortlist without changing the strict FOUR-FY screen.
Matches verified, corporate-action-adjusted completed EOD bars with exact-
date official NIFTY 50 TRI and the frozen quality-leader technical rules.

Never creates actionable alerts, quote bridge proposals or paper/live orders.
Two current FY fundamentals are NOT point-in-time historical audit evidence.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from nse_mainboard_discovery import CATALOG, write_csv_atomic
from research_monitor.discovered_annual_universe import verified_discovered_symbols
from research_monitor.paths import data_root
from research_monitor.quality_leader_rules import (
    PARAMETERS, STRATEGY_ID, quality_leader_features,
)
from research_monitor.schedule import IST, atomic_status
from research_monitor.strategy_sources import read_nifty50_tri
from research_monitor.termux_resources import memory_snapshot

OUTPUT = "fast_track_two_year_eod_research.csv"
STATUS = "runtime/fast_track_two_year_eod_research_status.json"
CURSOR = "runtime/fast_track_two_year_eod_cursor.json"
MAX_BARS_PER_SYMBOL = 350
MAX_SYMBOLS = 300
MAX_ROWS = 15
SOURCE_FILES = (
    "annual_fundamental_quality_report.csv",
    "fast_fundamental_shortlist.csv",
    "fast_fundamental_screen_summary.csv",
    "FY2425.csv",
    "NSE_FY2025-26_Consolidated_Fundamentals.csv",
    "market_ohlcv.csv",
    "market_data_metadata.json",
    "nifty50_tri_history.csv",
    "nifty50_tri_history_metadata.json",
)
FIELDS = [
    "Symbol", "Company", "Signal_Date", "EOD_Reference_Close",
    "Indicative_Next_Open_Min", "Indicative_Next_Open_Max",
    "Indicative_Stop", "Indicative_Target_2R", "Indicative_Target_3R",
    "Relative_Nifty50_TRI_63_Session", "Two_FY_Revenue_Growth_Pct",
    "Two_FY_Profit_Growth_Pct", "Two_FY_EPS_Growth_Pct",
    "Research_Scope", "Original_Four_Year_Strict_Screen_Changed",
    "Historically_Point_In_Time_Fundamentals_Verified",
    "Backtested_Win_Probability", "Paper_Trade_Eligible",
    "Live_Trade_Eligible", "Orders",
]

class FastTrackBlocked(RuntimeError):
    pass


def _read_csv(root: Path, name: str, required: set[str]) -> list[dict]:
    path = root/name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 25_000_000:
        raise FastTrackBlocked("SOURCE_MISSING_OR_UNSAFE:"+name)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rd = csv.DictReader(handle)
        if not rd.fieldnames or not required.issubset(rd.fieldnames):
            raise FastTrackBlocked("SOURCE_SCHEMA_INVALID:"+name)
        data = list(rd)
    if len(data) > 6000 or any(None in r for r in data):
        raise FastTrackBlocked("SOURCE_ROWS_INVALID:"+name)
    return data


def source_universe(root: Path, now: datetime) -> list[dict]:
    valid = verified_discovered_symbols(root, now=now)
    mainboard = _read_csv(root, CATALOG, {"Symbol","ISIN","Company","Series"})
    names = {}
    for row in mainboard:
        sym = row["Symbol"].strip().upper()
        if sym in names or row.get("Series") != "EQ" or sym not in valid:
            raise FastTrackBlocked("MAINBOARD_IDENTITY_AMBIGUOUS")
        names[sym] = row["Company"]
    if len(names) != len(valid):
        raise FastTrackBlocked("MAINBOARD_CATALOG_INCOMPLETE")
    quality = _read_csv(root,"annual_fundamental_quality_report.csv",
                        {"Symbol","Two_Year_Baseline_Data_Quality"})
    seen = set()
    qualifying = set()
    for row in quality:
        sym = row["Symbol"].strip().upper()
        if sym in seen:
            raise FastTrackBlocked("DUPLICATE_ANNUAL_QUALITY_SYMBOL")
        seen.add(sym)
        if sym in names and row["Two_Year_Baseline_Data_Quality"] == "PASS":
            qualifying.add(sym)
    report = _read_csv(root,"fast_fundamental_screen_summary.csv",
                       {"Report_Generated_On","Fast_Fundamental_Pass_Count","Status"})
    if len(report) != 1 or report[0]["Status"] != "PROVISIONAL_SCREEN_COMPLETE":
        raise FastTrackBlocked("PROVISIONAL_SCREEN_UNAVAILABLE")
    day = datetime.strptime(report[0]["Report_Generated_On"][:10],"%Y-%m-%d").date()
    age = (now.astimezone(IST).date()-day).days
    if age < 0 or age > 3:
        raise FastTrackBlocked("PROVISIONAL_SCREEN_STALE")
    shortlist = _read_csv(root,"fast_fundamental_shortlist.csv", {
        "Symbol","Fast_Fundamental_Pass","Research_Scope",
        "Revenue_Growth_Pct","Net_Profit_Growth_Pct","EPS_Growth_Pct"})
    verified = {}
    for row in shortlist:
        sym = row["Symbol"].strip().upper()
        if sym in verified:
            raise FastTrackBlocked("DUPLICATE_PROVISIONAL_RESEARCH_SYMBOL")
        if (row["Fast_Fundamental_Pass"].strip().lower() != "true"
            or row["Research_Scope"] != "PROVISIONAL_LOWER_CONFIDENCE_RESEARCH_ONLY"
            or sym not in qualifying):
            raise FastTrackBlocked("PROVISIONAL_AND_YEAR_QUALITY_DISAGREE")
        verified[sym] = {
            "Symbol":sym,"Company":names[sym],
            "Two_FY_Revenue_Growth_Pct":row["Revenue_Growth_Pct"],
            "Two_FY_Profit_Growth_Pct":row["Net_Profit_Growth_Pct"],
            "Two_FY_EPS_Growth_Pct":row["EPS_Growth_Pct"],
        }
    if int(report[0]["Fast_Fundamental_Pass_Count"]) != len(verified):
        raise FastTrackBlocked("PROVISIONAL_SUMMARY_COUNT_MISMATCH")
    return [verified[s] for s in sorted(verified)]


def _hash(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise FastTrackBlocked("SOURCE_MISSING_OR_SYMLINK")
    h=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def adjusted_bars(root: Path, symbols: set[str], now: datetime) -> dict[str,pd.DataFrame]:
    path = root/"market_ohlcv.csv"
    meta_path = root/"market_data_metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if (not isinstance(meta,dict)
        or meta.get("corporate_actions_adjusted") is not True
        or not str(meta.get("source","")).strip()
        or not str(meta.get("adjustment_method","")).strip()
        or meta.get("sha256") != _hash(path)):
        raise FastTrackBlocked("ADJUSTED_HISTORY_SOURCE_ATTESTATION_INVALID")
    store = defaultdict(lambda:deque(maxlen=MAX_BARS_PER_SYMBOL))
    today = now.astimezone(IST).date().isoformat()
    if path.stat().st_size > 850_000_000:
        raise FastTrackBlocked("ADJUSTED_EOD_FILE_UNBOUNDED")
    with path.open(encoding="utf-8-sig",newline="") as f:
        rd = csv.DictReader(f)
        needed={"Symbol","Date","Open","High","Low","Close","Volume"}
        if not rd.fieldnames or not needed.issubset(rd.fieldnames):
            raise FastTrackBlocked("ADJUSTED_EOD_COLUMNS_MISSING")
        for row in rd:
            sym=row["Symbol"].strip().upper()
            if sym in symbols and row["Date"] < today:
                store[sym].append(row)
    result={}
    for sym,history in store.items():
        if len(history) < PARAMETERS["min_bars"]:
            continue
        frame=pd.DataFrame(history)
        try:
            frame["Date"]=pd.to_datetime(frame["Date"],format="%Y-%m-%d",errors="raise")
            for key in ("Open","High","Low","Close","Volume"):
                frame[key]=pd.to_numeric(frame[key],errors="raise")
            dates=frame["Date"]
            data=frame[["Open","High","Low","Close","Volume"]]
            if (dates.duplicated().any() or not dates.is_monotonic_increasing
                or not math.isfinite(data.to_numpy(dtype=float)).all()
                or (data[["Open","High","Low","Close"]] <=0).any().any()
                or (data["Volume"]<0).any()
                or (data["High"]<data[["Open","Low","Close"]].max(axis=1)).any()
                or (data["Low"]>data[["Open","High","Close"]].min(axis=1)).any()
                or (dates.diff().dt.days.iloc[1:]>14).any()):
                continue
            last_age=(now.astimezone(IST).date()-dates.iloc[-1].date()).days
            if not 1<=last_age<=4:
                continue
            result[sym]=frame
        except (ValueError,TypeError,OverflowError):
            continue
    return result


def scan(root: Path, *, now:datetime|None=None,max_symbols:int=MAX_SYMBOLS,
         benchmark_loader=read_nifty50_tri,
         feature_builder=quality_leader_features,
         resource_probe=memory_snapshot) -> dict:
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    if not 1<=max_symbols<=MAX_SYMBOLS:
        raise ValueError("MAX_SYMBOLS_RANGE_1_TO_300")
    resources=resource_probe()
    if not resources.get("heavy_research_allowed"):
        raise FastTrackBlocked("RAM_PRESSURE_RESEARCH_DEFERRED")
    eligible=source_universe(root,now)
    # Rotate deterministic capped batches between daily maintenance runs.
    # The cursor is bound to the EXACT provisional symbol set, not a stale
    # count or an unrelated previous shortlist. Never skip a company silently.
    digest=hashlib.sha256("|".join(
        item["Symbol"] for item in eligible).encode("utf-8")).hexdigest()
    cursor_path=root/CURSOR
    try:
        previous=json.loads(cursor_path.read_text(encoding="utf-8"))
        start=(previous["next_offset"] if
               previous.get("universe_sha256")==digest else 0)
        if (not isinstance(start,int) or isinstance(start,bool)
            or start<0 or start>=len(eligible)):
            start=0
    except (OSError,ValueError,KeyError,TypeError):
        start=0
    batch=eligible[start:start+max_symbols]
    next_offset=(start+len(batch)) % len(eligible) if eligible else 0
    research=[]
    if batch:
        bars=adjusted_bars(root,{r["Symbol"] for r in batch},now)
        benchmark,_benchmark_sha=benchmark_loader(root)
        for item in batch:
            data=bars.get(item["Symbol"])
            if data is None:
                continue
            features=feature_builder(data,benchmark)
            if features.empty:
                continue
            last=features.iloc[-1]
            if not bool(last.Signal):
                continue
            close=float(last.Close)
            stop=float(last.Stop)
            relative=float(last.Relative_TRI_63d_Return)
            if (not all(math.isfinite(v) for v in (close,stop,relative))
                or not 0<stop<close or not .02<=(close-stop)/close<=.10):
                continue
            risk=close-stop
            research.append({
                "Symbol":item["Symbol"],
                "Company":item["Company"],
                "Signal_Date":str(pd.Timestamp(last.Date).date()),
                "EOD_Reference_Close":round(close,4),
                "Indicative_Next_Open_Min":round(close,4),
                "Indicative_Next_Open_Max":round(close*(1+PARAMETERS[
                    "max_next_session_entry_gap_above_signal_close"]),4),
                "Indicative_Stop":round(stop,4),
                "Indicative_Target_2R":round(close+2*risk,4),
                "Indicative_Target_3R":round(close+3*risk,4),
                "Relative_Nifty50_TRI_63_Session":round(relative,5),
                **{key:item[key] for key in (
                    "Two_FY_Revenue_Growth_Pct","Two_FY_Profit_Growth_Pct",
                    "Two_FY_EPS_Growth_Pct")},
                "Research_Scope":"PROVISIONAL_TWO_FY_EOD_ONLY_NO_TRADE_ALERT",
                "Original_Four_Year_Strict_Screen_Changed":"false",
                "Historically_Point_In_Time_Fundamentals_Verified":"false",
                "Backtested_Win_Probability":"",
                "Paper_Trade_Eligible":"false",
                "Live_Trade_Eligible":"false",
                "Orders":"DISABLED",
            })
    research.sort(key=lambda r:(-float(r["Relative_Nifty50_TRI_63_Session"]),
                                r["Symbol"]))
    report={
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"FAST_TRACK_PROVISIONAL_EOD_RESEARCH_UPDATED",
        "strategy":STRATEGY_ID,
        "provisional_two_fy_quality_symbols":len(eligible),
        "bounded_symbols_assessed_this_run":len(batch),
        "research_batch_start_offset":start,
        "research_batch_next_offset":next_offset,
        "universe_rotation_complete_this_run":(
            start+len(batch)>=len(eligible)),
        "qualified_adjusted_price_histories":len(bars) if batch else 0,
        "research_patterns_this_run":len(research),
        "research_rows_written":min(MAX_ROWS,len(research)),
        "coverage_is_full_provisional_universe":len(eligible)<=len(batch),
        "fundamentals_only_two_recent_fys_not_four_year_approval":True,
        "historical_fundamental_point_in_time_verified":False,
        "watchlist_is_live_trade_recommendation":False,
        "signal_probability_validated":False,
        "broker_quote_required_before_any_future_order_workflow":True,
        "trade_eligible":False,"orders":"DISABLED",
    }
    write_csv_atomic(root/OUTPUT,FIELDS,research[:MAX_ROWS])
    atomic_status(cursor_path,{"universe_sha256":digest,
                               "next_offset":next_offset})
    atomic_status(root/STATUS,report)
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-symbols",type=int,default=MAX_SYMBOLS)
    args=parser.parse_args()
    try:
        result=scan(data_root(),max_symbols=args.max_symbols)
    except (FastTrackBlocked,ValueError,OSError,KeyError,TypeError,
            csv.Error,OverflowError) as exc:
        result={
            "checked_at_utc":datetime.now(timezone.utc).isoformat(),
            "state":"FAST_TRACK_RESEARCH_BLOCKED",
            "reason":str(exc)[:180],
            "research_only":True,"trade_eligible":False,"orders":"DISABLED",
        }
        # Clear previously published research candidates if any source or
        # RAM safety gate stops the scan, avoiding stale research displays.
        write_csv_atomic(data_root()/OUTPUT,FIELDS,[])
        atomic_status(data_root()/STATUS,result)
        print(json.dumps(result,indent=2))
        return 1
    print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
