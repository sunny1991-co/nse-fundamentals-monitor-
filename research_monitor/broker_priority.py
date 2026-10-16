"""Two-broker NSE equity *market data* check: Groww first, Upstox second.

No trade endpoints, order placement, credential disclosure or account movement.
A fallback quote cannot authorize changing the EXECUTION broker. This module
runs only on explicit invocation; no unapproved historical strategy is used.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
import re

import requests

from research_monitor.risk import quote_errors
from research_monitor.upstox_data import UpstoxData, DataUnavailable

GROWW_QUOTE_URL = "https://api.groww.in/v1/live-data/quote"
# Independently check this documented NSE equity example. Instrument mapping
# for trading decisions MUST be obtained from broker instrument masters.
CHECK_SYMBOL = "RELIANCE"
CHECK_UPSTOX_KEY = "NSE_EQ|INE002A01018"
MAX_AGE_SECONDS = 15
MAX_SPREAD_FRACTION = .005
MAX_CROSS_FEED_DEVIATION_FRACTION = .01
PRIORITY = ("GROWW", "UPSTOX")


class QuoteUnavailable(RuntimeError):
    pass


def _positive_float(value):
    if isinstance(value,bool):
        raise ValueError("INVALID_QUOTE")
    x=float(value)
    if not math.isfinite(x) or x <= 0:
        raise ValueError("INVALID_QUOTE")
    return x


def _time(value):
    if isinstance(value,bool):
        raise ValueError("INVALID_QUOTE_TIMESTAMP")
    if isinstance(value,(int,float)):
        if not math.isfinite(value):
            raise ValueError("INVALID_QUOTE_TIMESTAMP")
        # Official Groww quote field last_trade_time is milliseconds.
        dt=datetime.fromtimestamp(float(value)/1000,timezone.utc)
    elif isinstance(value,str):
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
    else:
        raise ValueError("INVALID_QUOTE_TIMESTAMP")
    if dt.tzinfo is None:
        raise ValueError("TIMESTAMP_TIMEZONE_REQUIRED")
    return dt.isoformat()


class GrowwReadOnly:
    def __init__(self, token=None, get=None):
        self.token=token if token is not None else os.environ.get("GROWW_ACCESS_TOKEN")
        self.get=get or requests.get

    def quote(self, symbol, now=None):
        """Strictly one Groww GET, equity CASH only. No POST or fallback auth."""
        if not self.token:
            raise QuoteUnavailable("GROWW_API_TOKEN_OR_SUBSCRIPTION_REQUIRED")
        if not isinstance(symbol,str) or not re.fullmatch(
                r"[A-Z0-9&._-]{1,40}",symbol):
            raise QuoteUnavailable("GROWW_VERIFIED_NSE_SYMBOL_REQUIRED")
        now=now or datetime.now(timezone.utc)
        try:
            response=self.get(
                GROWW_QUOTE_URL,
                params={"exchange":"NSE","segment":"CASH",
                        "trading_symbol":symbol},
                headers={
                    "Authorization":"Bearer "+self.token,
                    "X-API-VERSION":"1.0",
                    "Accept":"application/json",
                },
                timeout=10,allow_redirects=False,
            )
            if response.status_code!=200:
                raise QuoteUnavailable("GROWW_HTTP_"+str(response.status_code))
            body=response.json()
            if not isinstance(body,dict) or body.get("status")!="SUCCESS":
                raise QuoteUnavailable("GROWW_PROVIDER_REJECTED_QUOTE")
            row=body.get("payload")
            if not isinstance(row,dict):
                raise ValueError("QUOTE_SCHEMA_INVALID")
            result={
                "source":"GROWW",
                "symbol":symbol,
                "exchange":"NSE",
                "ltp":_positive_float(row["last_price"]),
                "bid":_positive_float(row["bid_price"]),
                "ask":_positive_float(row["offer_price"]),
                "feed_time":_time(row["last_trade_time"]),
            }
            errors=quote_errors(result,{
                "max_quote_age_seconds":MAX_AGE_SECONDS,
                "max_spread_fraction":MAX_SPREAD_FRACTION,
            },now)
            result["validation_errors"]=errors
            return result
        except QuoteUnavailable:
            raise
        except (requests.RequestException,ValueError,TypeError,KeyError,
                OverflowError,AttributeError, OSError):
            # Do not print response bodies or any credentials or URL query secrets.
            raise QuoteUnavailable("GROWW_CONNECTION_OR_QUOTE_SCHEMA_ERROR") from None


def dual_quote_check(now=None, *, groww=None, upstox=None,
                     symbol=CHECK_SYMBOL, upstox_key=CHECK_UPSTOX_KEY):
    """Probe both sources independently, then choose a fresh comparable quote.

    A quote from Upstox is data-only; Groww trading is never silently
    substituted by Upstox trading. Two apparently fresh quotes with >1%
    disagreement are BLOCKED, not resolved by arbitrary source preference.
    """
    now=now or datetime.now(timezone.utc)
    groww=groww or GrowwReadOnly()
    upstox=upstox or UpstoxData()
    report={
        "scope":"READ_ONLY_DUAL_BROKER_NSE_EQUITY_QUOTE_CHECK",
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "primary":"GROWW",
        "secondary":"UPSTOX",
        "execution_preference":"GROWW_ONLY",
        "automatic_broker_trade_fallback":False,
        "groww":{
            "credential_present":bool(groww.token),
            "quote_status":"NOT_CHECKED",
            "fresh":False,
        },
        "upstox":{
            "credential_present":bool(upstox.token),
            "quote_status":"NOT_CHECKED",
            "fresh":False,
        },
        "chosen_data_provider":None,
        "validation":"NO_FRESH_COMPARABLE_QUOTES",
        "live_orders":"DISABLED",
        "trading_strategy_approved":False,
        "user_yes_no_order_execution":"NOT_ENABLED",
        "live_eligible":False,
    }
    data={}
    if groww.token:
        try:
            q=groww.quote(symbol,now)
            report["groww"]["quote_status"]=(
                "OK_FRESH" if not q["validation_errors"] else
                "REJECTED_"+"_".join(q["validation_errors"])
            )
            report["groww"]["fresh"]=not q["validation_errors"]
            if not q["validation_errors"]:
                data["GROWW"]=q
        except QuoteUnavailable as exc:
            report["groww"]["quote_status"]=str(exc)
    else:
        report["groww"]["quote_status"]="GROWW_SUBSCRIPTION_OR_TOKEN_MISSING"
    if upstox.token:
        try:
            # Use existing vetted Upstox normalization; does not place orders.
            quotes=upstox.quotes([upstox_key],now)
            if len(quotes)!=1 or quotes[0]["token"]!=upstox_key:
                raise DataUnavailable("UPSTOX_QUOTE_NOT_MATCHED")
            q=quotes[0]
            report["upstox"]["quote_status"]=(
                "OK_FRESH" if not q["validation_errors"] else
                "REJECTED_"+"_".join(q["validation_errors"])
            )
            report["upstox"]["fresh"]=not q["validation_errors"]
            if not q["validation_errors"]:
                data["UPSTOX"]=q
        except DataUnavailable as exc:
            report["upstox"]["quote_status"]=str(exc)
    else:
        report["upstox"]["quote_status"]="UPSTOX_ANALYTICS_TOKEN_MISSING"

    if data.get("GROWW") and data.get("UPSTOX"):
        a=float(data["GROWW"]["ltp"])
        b=float(data["UPSTOX"]["ltp"])
        gap=abs(a-b)/max(a,b)
        report["cross_provider_price_difference_pct"]=round(gap*100,3)
        if gap>MAX_CROSS_FEED_DEVIATION_FRACTION:
            report["validation"]="CROSS_PROVIDER_PRICE_MISMATCH_BLOCKED"
            return report
    if data.get("GROWW"):
        report["chosen_data_provider"]="GROWW"
        report["validation"]="FRESH_GROWW_QUOTE_RESEARCH_ONLY"
    elif data.get("UPSTOX"):
        report["chosen_data_provider"]="UPSTOX"
        report["validation"]="FRESH_UPSTOX_FALLBACK_RESEARCH_ONLY"
    return report
