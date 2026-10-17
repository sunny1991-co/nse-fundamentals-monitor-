#!/usr/bin/env python3
"""Automated technical + filing-evidence *research radar* for all NSE mainboard EQ.

The unadjusted exchange archive is good for *candidate discovery only*.
Corporate actions / historical entity and point-in-time fundamental audits
are not fully attested. NEVER generate order candidates or claimed win rates.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3

from nse_mainboard_discovery import CATALOG, write_csv_atomic
from nse_mainboard_history_backfill import DATABASE
from research_monitor.discovered_annual_universe import verified_discovered_symbols
from research_monitor.paths import data_root
from research_monitor.schedule import IST,atomic_status

OUTPUT = "nse_mainboard_auto_research_radar.csv"
STATUS = "runtime/nse_mainboard_auto_research_radar_status.json"
FIELDNAMES = [
    "Symbol","ISIN","Company","Baseline_Tracked",
    "NSE_Verified_Annual_Years","Strict_Annual_Pass","Strict_Final_Fundamental_Pass",
    "Unadjusted_Daily_Bars","First_Bar","Last_Bar",
    "Close_Raw_Not_Corporate_Adjusted","Raw_Return_63_Sessions_Pct",
    "Raw_MA50","Raw_MA200","Raw_Volume_Ratio_To_Prior20",
    "Raw_Avg_Turnover_Prior20_Rs",
    "Technical_Research_Pattern","Fundamental_Research_State",
    "Research_State","Next_Data_Action","Trade_Eligible",
]
MAX_BARS_PER_STOCK = 270
MIN_TECHNICAL_BARS = 260


class RadarUnready(RuntimeError):
    pass


def rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f))


def truths(root:Path,filename:str,flag:str)->set[str]:
    return {r.get("Symbol","").strip().upper() for r in rows(root/filename)
            if str(r.get(flag,"")).strip().lower() in ("true","1","yes")}


def audited_years(root:Path)->dict[str, list[str]]:
    years={}
    for r in rows(root/"annual_historical_results.csv"):
        s=r.get("Symbol","").strip().upper()
        fy=r.get("Financial_Year","").strip()
        if (not s or fy not in ("2022-23","2023-24","2024-25","2025-26")
            or str(r.get("NSE_Consolidation_Verified","")).lower() not in ("true","1","yes")
            or str(r.get("Reporting_Basis","")).lower()!="consolidated"
            or "unaudit" in str(r.get("Audit_Status","")).lower()
            or "audit" not in str(r.get("Audit_Status","")).lower()
            or not str(r.get("Source_URL","")).startswith("https://nsearchives.nseindia.com/")):
            continue
        years.setdefault(s,set()).add(fy)
    return {s:sorted(value) for s,value in years.items()}


def get_recent_bars(db:sqlite3.Connection,symbol:str)->list[tuple]:
    result=db.execute(
        """SELECT day,open,high,low,close,volume FROM unadjusted_eq_bars
           WHERE symbol=? ORDER BY day DESC LIMIT ?""",
        (symbol,MAX_BARS_PER_STOCK),
    ).fetchall()
    return list(reversed(result))


def raw_technical(bars:list[tuple], now:datetime)->dict:
    info={
        "Unadjusted_Daily_Bars":len(bars),
        "First_Bar":bars[0][0] if bars else "",
        "Last_Bar":bars[-1][0] if bars else "",
        "Close_Raw_Not_Corporate_Adjusted":"",
        "Raw_Return_63_Sessions_Pct":"",
        "Raw_MA50":"","Raw_MA200":"",
        "Raw_Volume_Ratio_To_Prior20":"",
        "Raw_Avg_Turnover_Prior20_Rs":"",
        "Technical_Research_Pattern":"UNADJUSTED_HISTORY_INSUFFICIENT",
    }
    if len(bars)<MIN_TECHNICAL_BARS:
        return info
    from datetime import date
    try:
        last=date.fromisoformat(bars[-1][0])
        days=(now.astimezone(IST).date()-last).days
        dates=[date.fromisoformat(b[0]) for b in bars]
        if any((r-l).days>14 or r<=l for l,r in zip(dates,dates[1:])):
            info["Technical_Research_Pattern"]="HISTORY_GAP_OR_DUPLICATE_REVIEW"
            return info
        if days<1 or days>8:
            info["Technical_Research_Pattern"]="LATEST_COMPLETE_BAR_STALE_OR_NOT_FINISHED"
            return info
        close=[float(b[4]) for b in bars]
        high=[float(b[2]) for b in bars]
        volume=[float(b[5]) for b in bars]
        if (any(not math.isfinite(x) or x<=0 for x in close)
            or any(not math.isfinite(x) or x<=0 for x in high)
            or any(not math.isfinite(x) or x<0 for x in volume)):
            info["Technical_Research_Pattern"]="INVALID_UNADJUSTED_PRICE_BAR"
            return info
        lastclose=close[-1]
        gain=(lastclose/close[-64]-1)*100
        ma50=sum(close[-50:])/50
        ma200=sum(close[-200:])/200
        v20=sum(volume[-21:-1])/20
        vratio=volume[-1]/v20 if v20>0 else 0
        turnover=sum(close[-21+i]*volume[-21+i] for i in range(20))/20
        info.update({
            "Close_Raw_Not_Corporate_Adjusted":round(lastclose,4),
            "Raw_Return_63_Sessions_Pct":round(gain,3),
            "Raw_MA50":round(ma50,4),"Raw_MA200":round(ma200,4),
            "Raw_Volume_Ratio_To_Prior20":round(vratio,3),
            "Raw_Avg_Turnover_Prior20_Rs":round(turnover,2),
        })
        if (gain>=10 and lastclose>ma50>ma200
            and lastclose>max(high[-31:-1])
            and vratio>=1.2 and turnover>=50_000_000):
            info["Technical_Research_Pattern"]="UNADJUSTED_BREAKOUT_REVIEW_ONLY"
        else:
            info["Technical_Research_Pattern"]="NO_RAW_BREAKOUT_RESEARCH_PATTERN"
    except (TypeError,ValueError,ZeroDivisionError,OverflowError):
        info["Technical_Research_Pattern"]="INVALID_UNADJUSTED_PRICE_BAR"
    return info


def scan(root:Path,*,now:datetime|None=None)->dict:
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    official=verified_discovered_symbols(root,now=now)
    catalog=rows(root/CATALOG)
    if len(catalog)!=len(official) or any(r.get("Symbol") not in official for r in catalog):
        raise RadarUnready("OFFICIAL_CATALOG_COUNT_OR_MEMBERSHIP_CHANGED")
    if not (root/DATABASE).is_file():
        raise RadarUnready("RESEARCH_EOD_BACKFILL_NOT_STARTED")
    strict=truths(root,"annual_fundamental_quality_report.csv","Full_Fundamental_Criteria_Pass")
    final={r.get("Symbol","").strip().upper() for r in rows(root/"final_fundamental_shortlist.csv")}
    history=audited_years(root)
    counts={}
    out=[]
    with sqlite3.connect(root/DATABASE,timeout=20) as db:
        for listed in sorted(catalog,key=lambda r:r["Symbol"]):
            sym=listed["Symbol"]
            prior=get_recent_bars(db,sym)
            tech=raw_technical(prior,now)
            annual=sym in strict
            final_pass=annual and sym in final
            verified=history.get(sym,[])
            if not annual:
                if len(verified)<4:
                    fund="MISSING_FOUR_FULLY_VERIFIED_ANNUAL_YEARS"
                else:
                    fund="STRICT_FUNDAMENTAL_CRITERIA_NOT_PASSED"
            elif not final_pass:
                fund="QUARTERLY_OR_FINAL_FUNDAMENTAL_SCREEN_PENDING"
            else:
                fund="EXISTING_STRICT_FUNDAMENTAL_RESEARCH_EVIDENCE"
            if tech["Technical_Research_Pattern"]=="UNADJUSTED_BREAKOUT_REVIEW_ONLY":
                state="TECHNICAL_CANDIDATE_REVIEW_ONLY"
                action="VERIFY_CORPORATE_ACTIONS_AND_FULL_FUNDAMENTALS_BEFORE_RESEARCH_SIGNAL"
            elif tech["Unadjusted_Daily_Bars"]<MIN_TECHNICAL_BARS:
                state="PRICE_HISTORY_BACKFILL_IN_PROGRESS"
                action="COLLECT_MORE_OFFICIAL_EOD_AND_VERIFY_ADJUSTMENTS"
            else:
                state="RESEARCH_MONITORING_NO_VERIFIED_TRADE_SIGNAL"
                action="RECHECK_AFTER_COMPLETED_EOD_AND_FILING_UPDATES"
            counts[state]=counts.get(state,0)+1
            out.append({
                "Symbol":sym,"ISIN":listed["ISIN"],"Company":listed.get("Company",""),
                "Baseline_Tracked":listed.get("Baseline_Tracked","false"),
                "NSE_Verified_Annual_Years":"|".join(verified),
                "Strict_Annual_Pass":str(annual).lower(),
                "Strict_Final_Fundamental_Pass":str(final_pass).lower(),
                **tech,
                "Fundamental_Research_State":fund,
                "Research_State":state,
                "Next_Data_Action":action,
                "Trade_Eligible":"false",
            })
    write_csv_atomic(root/OUTPUT,FIELDNAMES,out)
    report={
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"NSE_MAINBOARD_AUTO_RESEARCH_RADAR_UPDATED",
        "mainboard_eq_companies":len(official),
        "research_states":counts,
        "unadjusted_breakout_research_candidates":counts.get("TECHNICAL_CANDIDATE_REVIEW_ONLY",0),
        "raw_prices_are_not_corporate_action_adjusted":True,
        "price_history_and_fundamentals_must_be_verified_separately":True,
        "not_a_verified_strategy_or_win_rate":True,
        "trade_eligible_companies":0,
        "orders":"DISABLED",
    }
    atomic_status(root/STATUS,report)
    return report


def main()->int:
    try:
        result=scan(data_root())
    except (RadarUnready,OSError,ValueError,sqlite3.Error,KeyError) as exc:
        result={"checked_at_utc":datetime.now(timezone.utc).isoformat(),
                "state":"NSE_MAINBOARD_RESEARCH_RADAR_BLOCKED",
                "reason":str(exc)[:180],
                "previous_research_report_preserved":True,"orders":"DISABLED"}
        atomic_status(data_root()/STATUS,result)
        print(json.dumps(result,indent=2))
        return 1
    print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
