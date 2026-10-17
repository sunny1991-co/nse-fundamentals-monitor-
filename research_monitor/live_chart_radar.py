"""Automatic read-only 1-minute OHLCV chart research using Upstox V3.

A V3 intraday GET provides exchange-timeframe candles, NOT sampled LTP ticks.
Up to four *verified current NSE mainboard EQ* instruments per minute are
monitored during a verified open session. Indicator patterns are observational,
not approved trading instructions, predicted probabilities or broker orders.
No token or HTTP body is ever written to local report output.
"""
from __future__ import annotations

import csv
from datetime import datetime,timedelta,timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import quote

import requests

from nse_mainboard_discovery import CATALOG
from research_monitor.discovered_annual_universe import verified_discovered_symbols
from research_monitor.schedule import IST,atomic_status

STATUS="runtime/live_chart_research_status.json"
MAX_SYMBOLS=4
MIN_CANDLES=65
INTERVAL_SECONDS=60
BLOCK_COOLDOWN_SECONDS=300
SYMBOL=re.compile(r"^[A-Z0-9&._-]{1,40}$")
ISIN=re.compile(r"^IN[A-Z0-9]{10}$")
SOURCE="UPSTOX_V3_1MIN_INTRADAY_CANDLES"
CAP=390
PAGE_PLOT_BARS=50


class ChartUnavailable(RuntimeError):
    pass


def _read(path):
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError,TypeError):
        return {}


def _table(path):
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig",newline="") as stream:
        return list(csv.DictReader(stream))


def universe(root, now):
    verified=verified_discovered_symbols(root,now=now)
    rows=_table(root/CATALOG)
    if len(rows)!=len(verified):
        raise ChartUnavailable("VERIFIED_EQ_CATALOG_MISMATCH")
    all_by_symbol={}
    for row in rows:
        symbol=row.get("Symbol","")
        isin=row.get("ISIN","")
        if (symbol not in verified or not SYMBOL.fullmatch(symbol)
                or not ISIN.fullmatch(isin) or row.get("Series")!="EQ"
                or symbol in all_by_symbol):
            raise ChartUnavailable("EQ_SYMBOL_ISIN_SOURCE_MISMATCH")
        all_by_symbol[symbol]=row
    return all_by_symbol


def choose_symbols(root, official):
    """Bounded, deterministic priority. Never upgrades a raw research pattern."""
    radar=_table(root/"nse_mainboard_auto_research_radar.csv")
    prioritized=[]
    for row in radar:
        symbol=row.get("Symbol")
        if symbol not in official or row.get("ISIN")!=official[symbol]["ISIN"]:
            continue
        # Use only an explicitly labelled raw breakout as top priority.
        if row.get("Technical_Research_Pattern")=="UNADJUSTED_BREAKOUT_REVIEW_ONLY":
            prioritized.append(symbol)
    preferred=os.environ.get("NSE_CHART_SYMBOLS","")
    if preferred:
        chosen=[x.strip().upper() for x in preferred.split(",") if x.strip()]
        if (len(chosen)>MAX_SYMBOLS or len(set(chosen))!=len(chosen)
            or any(x not in official for x in chosen)):
            raise ChartUnavailable("CHART_SYMBOL_SELECTION_NOT_IN_VERIFIED_NSE_EQ")
        return chosen
    # First pilot is a well-audited candidate for source page review, not
    # a claim of fundamental PASS or near-term upside.
    seed=["20MICRONS"] if "20MICRONS" in official else []
    # Dynamic candidates from raw chart radar, then largest bar coverage;
    # at most four fetches total each minute and zero live orders.
    by_coverage=sorted(
        (r for r in radar if r.get("Symbol") in official
         and official[r["Symbol"]]["ISIN"]==r.get("ISIN")),
        key=lambda r:(-int(r.get("Unadjusted_Daily_Bars") or 0),r["Symbol"])
    )
    for sym in prioritized+seed+[r["Symbol"] for r in by_coverage]:
        if sym not in seed and sym not in prioritized and sym not in official:
            continue
        if sym not in seed:
            seed.append(sym)
        if len(seed)>=MAX_SYMBOLS:
            break
    if not seed:
        seed=sorted(official)[:MAX_SYMBOLS]
    return seed[:MAX_SYMBOLS]


def _finite(x):
    if isinstance(x,bool):
        raise ValueError("BOOLEAN_OHLCV_FIELD")
    x=float(x)
    if not math.isfinite(x):
        raise ValueError("NONFINITE_OHLCV_FIELD")
    return x


def normalize_candles(raw,symbol,now):
    if not isinstance(raw,list) or len(raw)>CAP:
        raise ChartUnavailable("INVALID_INTRADAY_CANDLE_ARRAY")
    localnow=now.astimezone(IST)
    day=localnow.date()
    selected={}
    for bar in raw:
        if not isinstance(bar,list) or len(bar)<6:
            raise ChartUnavailable("INVALID_INTRADAY_CANDLE_SCHEMA")
        try:
            stamp=datetime.fromisoformat(str(bar[0]))
            if stamp.tzinfo is None:
                raise ValueError("TIMEZONE_REQUIRED")
            op,hi,lo,cl,vol=(_finite(k) for k in bar[1:6])
        except (ValueError,TypeError,OverflowError) as exc:
            raise ChartUnavailable("INVALID_CANDLE_FIELDS") from exc
        if (stamp.astimezone(IST).date()!=day or
            not(0<lo<=min(op,cl)<=max(op,cl)<=hi) or vol<0):
            raise ChartUnavailable("INVALID_CURRENT_SESSION_EQUITY_CANDLE")
        if stamp>now:
            raise ChartUnavailable("FUTURE_CANDLE")
        # A full minute's OHLCV is accepted only when COMPLETE; never
        # use an in-progress candle as a validated chart breakout.
        if stamp+timedelta(minutes=1)>now:
            continue
        key=stamp.astimezone(timezone.utc).isoformat()
        if key in selected:
            raise ChartUnavailable("DUPLICATE_CANDLE_TIMESTAMP")
        selected[key]={
            "t":stamp.astimezone(IST).isoformat(),
            "o":op,"h":hi,"l":lo,"c":cl,"v":vol,
        }
    out=sorted(selected.values(),key=lambda x:x["t"])
    if len(out)<MIN_CANDLES:
        raise ChartUnavailable("INSUFFICIENT_COMPLETE_ONE_MINUTE_CANDLES")
    last=datetime.fromisoformat(out[-1]["t"])
    if not 0 <= (now-last).total_seconds() <= 240:
        raise ChartUnavailable("LATEST_CLOSED_CANDLE_TOO_OLD")
    if any((datetime.fromisoformat(b["t"])-datetime.fromisoformat(a["t"])).total_seconds()>600
           for a,b in zip(out[-55:-1],out[-54:])):
        raise ChartUnavailable("RECENT_INTRADAY_CANDLE_GAP")
    return out


def _ema(seq,n):
    if len(seq)<n:
        raise ChartUnavailable("EMA_WARMUP_MISSING")
    value=sum(seq[:n])/n
    result=[None]*(n-1)+[value]
    for val in seq[n:]:
        value+=2/(n+1)*(val-value)
        result.append(value)
    return result


def _rsi(values):
    if len(values)<16:
        raise ChartUnavailable("RSI_WARMUP_MISSING")
    delta=[b-a for a,b in zip(values,values[1:])]
    ups=sum(max(0,v) for v in delta[:14])/14
    downs=sum(max(0,-v) for v in delta[:14])/14
    for v in delta[14:]:
        ups=(ups*13+max(v,0))/14
        downs=(downs*13+max(-v,0))/14
    if ups==0 and downs==0:
        return 50.0
    if downs==0:
        return 100.0
    return 100-100/(1+ups/downs)


def analyze(symbol,rows):
    close=[r["c"] for r in rows]
    volume=[r["v"] for r in rows]
    ema20=_ema(close,20)[-1]
    ema50=_ema(close,50)[-1]
    ema12=_ema(close,12)
    ema26=_ema(close,26)
    macd=[a-b for a,b in zip(ema12,ema26) if a is not None and b is not None]
    line=macd[-1]
    signal=_ema(macd,9)[-1]
    macd_hist=line-signal
    rsi=_rsi(close)
    typical=[(r["h"]+r["l"]+r["c"])/3 for r in rows]
    v_total=sum(volume)
    vwap=(sum(v*p for v,p in zip(volume,typical))/v_total
          if v_total>0 else None)
    support=min(r["l"] for r in rows[-21:-1])
    resistance=max(r["h"] for r in rows[-21:-1])
    avgvol=sum(volume[-21:-1])/20
    volratio=volume[-1]/avgvol if avgvol>0 else None
    bullish=(close[-1]>ema20>ema50 and macd_hist>0
             and 52<=rsi<=78 and close[-1]>resistance
             and volratio is not None and volratio>=1.2)
    bearish=(close[-1]<ema20<ema50 and macd_hist<0 and rsi<45)
    state=("BULLISH_BREAKOUT_REVIEW_ONLY" if bullish else
           "BEARISH_WEAKNESS_REVIEW_ONLY" if bearish else
           "NO_VERIFIED_CHART_PATTERN")
    return {
        "symbol":symbol,"last_completed_minute":rows[-1]["t"],
        "close":round(close[-1],4),
        "ema20":round(ema20,4),"ema50":round(ema50,4),
        "rsi14":round(rsi,2),
        "macd":round(line,4),"macd_signal":round(signal,4),
        "macd_histogram":round(macd_hist,4),
        "session_vwap":round(vwap,4) if vwap is not None else None,
        "previous_20_bar_support":round(support,4),
        "previous_20_bar_resistance":round(resistance,4),
        "last_volume_to_previous20_ratio":round(volratio,3) if volratio is not None else None,
        "research_pattern":state,
        "research_only":True,"trade_eligible":False,
        "real_orders":"DISABLED",
        "chart_last_bars":rows[-PAGE_PLOT_BARS:],
    }


def fetch_1m(session,token,isin):
    if not ISIN.fullmatch(isin):
        raise ChartUnavailable("UNVERIFIED_INSTRUMENT_ISIN")
    url="https://api.upstox.com/v3/historical-candle/intraday/"+quote("NSE_EQ|"+isin,safe="")+"/minutes/1"
    try:
        res=session.get(url,
            headers={"Authorization":"Bearer "+token,"Accept":"application/json"},
            timeout=(4,7),allow_redirects=False)
        if res.status_code in (401,403,429):
            raise ChartUnavailable("UPSTOX_INTRADAY_ACCESS_BLOCKED_"+str(res.status_code))
        if res.status_code!=200:
            raise ChartUnavailable("UPSTOX_INTRADAY_HTTP_"+str(res.status_code))
        if len(res.content)>1_000_000:
            raise ChartUnavailable("INTRADAY_RESPONSE_TOO_LARGE")
        body=res.json()
    except requests.RequestException as exc:
        raise ChartUnavailable("UPSTOX_INTRADAY_CONNECTION_ERROR") from exc
    except (ValueError,TypeError) as exc:
        raise ChartUnavailable("UPSTOX_INTRADAY_JSON_INVALID") from exc
    if not isinstance(body,dict) or body.get("status")!="success" or not isinstance(body.get("data"),dict):
        raise ChartUnavailable("UPSTOX_INTRADAY_RESPONSE_NOT_SUCCESS")
    return body["data"].get("candles")


def run(root,now=None,*,open_session=False,session=None,market_open=None):
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("AWARE_CLOCK_REQUIRED")
    path=root/STATUS
    prior=_read(path)
    if not open_session:
        if prior.get("state")!="EXCHANGE_NOT_LIVE_NO_NEW_CHART_CANDLES":
            atomic_status(path,{
                "state":"EXCHANGE_NOT_LIVE_NO_NEW_CHART_CANDLES",
                "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
                "live_feed_verified":False,"charts":[],
                "orders":"DISABLED",
            })
        return {"state":"EXCHANGE_NOT_LIVE_NO_NEW_CHART_CANDLES","charts_count":0,
                "orders":"DISABLED"}
    # A single minute interval prevents multiplying calls by the swing 10s loop.
    try:
        earlier=datetime.fromisoformat(prior["last_poll_utc"])
        if earlier.tzinfo is not None and 0<=(now-earlier).total_seconds()<(
            BLOCK_COOLDOWN_SECONDS if prior.get("source_blocked") else INTERVAL_SECONDS
        ):
            return {"state":prior.get("state","UNKNOWN"),
                    "charts_count":len(prior.get("charts") or []),
                    "refresh_due":False,"orders":"DISABLED"}
    except (KeyError,ValueError,TypeError):
        pass
    status={
        "state":"NO_VERIFIED_INTRADAY_CHART_FEED",
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "last_poll_utc":now.astimezone(timezone.utc).isoformat(),
        "provider":SOURCE,
        "refresh_interval_seconds":INTERVAL_SECONDS,
        "maximum_symbols_polled_per_cycle":MAX_SYMBOLS,
        "is_live_minute_close_feed":False,
        "source_blocked":False,
        "charts":[],
        "verified_chart_candles_are_not_verified_fundamentals":True,
        "financial_screen_passes_added":0,
        "broker_orders_enabled":False,
        "orders":"DISABLED",
    }
    token=os.environ.get("UPSTOX_ANALYTICS_TOKEN","")
    if not token:
        status["state"]="UPSTOX_ANALYTICS_TOKEN_NOT_CONFIGURED"
        atomic_status(path,status)
        return status
    own=session is None
    session=session or requests.Session()
    try:
        # Upstox market-status must agree with the independent reviewed
        # exchange calendar; chart candles alone are not proof of live market.
        if market_open is None:
            from research_monitor.upstox_data import UpstoxData
            market_open=UpstoxData(token=token,get=session.get).is_open()
        if market_open is not True:
            raise ChartUnavailable("EXCHANGE_NOT_NORMAL_OPEN")
        official=universe(root,now)
        selected=choose_symbols(root,official)
        if not selected:
            raise ChartUnavailable("NO_VERIFIED_MAINBOARD_RESEARCH_SYMBOLS")
        status["symbols_selected"]=selected
        for sym in selected:
            try:
                candles=normalize_candles(fetch_1m(session,token,official[sym]["ISIN"]),sym,now)
                summary=analyze(sym,candles)
                status["charts"].append(summary)
            except ChartUnavailable as exc:
                msg=str(exc)
                status.setdefault("symbol_review_errors",[]).append({
                    "symbol":sym,"reason":msg[:80]})
                if "ACCESS_BLOCKED" in msg:
                    status["source_blocked"]=True
                    break
        status["state"]=(
            "READ_ONLY_LIVE_CHART_RESEARCH" if status["charts"]
            else "NO_FRESH_VERIFIED_COMPLETE_INTRADAY_CHARTS"
        )
        status["is_live_minute_close_feed"]=bool(status["charts"])
    except ChartUnavailable as exc:
        status["state"]=str(exc)[:80]
        status["source_blocked"]="ACCESS_BLOCKED" in status["state"]
    finally:
        if own:
            session.close()
        atomic_status(path,status)
    return status
