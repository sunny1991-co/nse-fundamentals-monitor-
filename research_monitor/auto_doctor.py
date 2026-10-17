"""Fail-closed, bounded self-healing project guardian for supervised Termux research.

Unattended rules can restart ONLY a specific, *already supervised* local child
after repeated stale-heartbeat proof and a cooldown. They NEVER alter
financial source data, APIs, .env, trading permissions, orders, git or code.
Optional model diagnoses sanitized incident *codes* without tool authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from urllib.request import urlopen
from urllib.error import URLError
from typing import Any

import requests

from research_monitor.schedule import atomic_status

STATUS="runtime/project_auto_doctor_status.json"
JOURNAL="runtime/project_auto_doctor_recovery_journal.json"
INTERVAL_SECONDS=120
MIN_CHILD_UPTIME=10*60
PERSISTENT_INCIDENT_CONFIRMATIONS=2
MIN_CONFIRMATION_GAP_SECONDS=100
RESTART_COOLDOWN_SECONDS=30*60
RESTART_WINDOW_SECONDS=6*60*60
MAX_RESTARTS_PER_COMPONENT_PER_WINDOW=2
LOW_DISK_BYTES=600*1024*1024
HEARTBEATS={
    "worker":("worker_status.json","updated_at",10*60),
    "history":("history_worker_status.json","updated_at",2*60*60),
    "research":("research_worker_status.json","updated_at",3*60*60),
    "upstox_reference":("upstox_reference_worker_status.json","updated_at_utc",3*60*60),
}
PROTECTED_FILES=("annual_historical_results.csv","FY2425.csv",
                 "NSE_FY2025-26_Consolidated_Fundamentals.csv",
                 "final_fundamental_shortlist.csv","swing_watchlist.json")
MAX_ISSUES=18


def read_json(path:Path)->dict:
    try:
        if path.is_symlink() or path.stat().st_size>2_000_000:
            return {}
        raw=json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw,dict) else {}
    except (OSError,ValueError,TypeError):
        return {}


def age_seconds(value:str,now:datetime)->float|None:
    try:
        dt=datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return None
        age=(now-dt).total_seconds()
        return age if age>=0 else None
    except (ValueError,TypeError):
        return None


def dashboard_ok(*,probe=None)->bool:
    """Fixed loopback-only MFA app health probe. No external URL/network target."""
    if probe is not None:
        return bool(probe())
    try:
        with urlopen("http://127.0.0.1:8787/healthz",timeout=1.5) as response:
            return response.status==200
    except (OSError,URLError,ValueError):
        return False


def inspect(root:Path,children:dict,births:dict,
            *,now:datetime|None=None,epoch:float|None=None,probe=None)->list[dict]:
    """Return opaque *issue codes* only, no tokens, financials, paths or PIDs."""
    now=now or datetime.now(timezone.utc)
    epoch=epoch if epoch is not None else now.timestamp()
    runtime=Path(root)/"runtime"
    issues=[]
    for name,(filename,key,threshold) in HEARTBEATS.items():
        child=children.get(name)
        if child is None or child.poll() is not None:
            issues.append({"code":"SUPERVISOR_CHILD_NOT_RUNNING","component":name,
                           "severity":"WARNING","auto_repair":False})
            continue
        if epoch-births.get(name,epoch)<MIN_CHILD_UPTIME:
            continue
        record=read_json(runtime/filename)
        age=age_seconds(record.get(key),now)
        # The market worker can legitimately run long postmarket analysis,
        # which blocks its usual 10s heartbeat until subprocess completion.
        if name=="worker" and record.get("analysis")=="RUNNING":
            threshold=3*60*60
        if age is None or age>threshold:
            issues.append({"code":"SUPERVISED_WORKER_HEARTBEAT_STALE",
                           "component":name,"severity":"ERROR",
                           "auto_repair":True})
    dash=children.get("dashboard")
    if dash is not None:
        if dash.poll() is None and epoch-births.get("dashboard",epoch)>=MIN_CHILD_UPTIME:
            if not dashboard_ok(probe=probe):
                issues.append({"code":"SECURE_DASHBOARD_HEALTH_UNAVAILABLE",
                               "component":"dashboard","severity":"ERROR",
                               "auto_repair":True})
        elif dash.poll() is not None:
            issues.append({"code":"SUPERVISOR_CHILD_NOT_RUNNING",
                           "component":"dashboard","severity":"WARNING",
                           "auto_repair":False})
    # Unknown/correctable source problems are tracked but never 'fixed' by
    # concealing API block errors or approving unverified results.
    source_files=[
        ("mainboard_raw_research_history_status.json","SOURCE_BACKFILL_BLOCKED",
         ("NSE_ACCESS_BLOCKED","MAINBOARD_RESEARCH_BACKFILL_BLOCKED","ARCHIVE_MISSING_BATCH_REVIEW")),
        ("nse_official_annual_pdf_collector_status.json","ANNUAL_SOURCE_BLOCKED",
         ("NSE_OFFICIAL_ANNUAL_INDEX_ACCESS_BLOCKED","OFFICIAL_ANNUAL_INDEX_BLOCKED")),
        ("nse_annual_pdf_section_review_status.json","PDF_REVIEW_BLOCKED",
         ("ARCHIVED_PDF_SECTION_REVIEW_BLOCKED",)),
    ]
    for path,code,stuck in source_files:
        record=read_json(runtime/path)
        if record.get("state") in stuck:
            issues.append({"code":code,"component":"data_source",
                           "severity":"WARNING","auto_repair":False})
    try:
        if shutil.disk_usage(root).free<LOW_DISK_BYTES:
            issues.append({"code":"DISK_HEADROOM_LOW","component":"storage",
                           "severity":"WARNING","auto_repair":False})
    except OSError:
        issues.append({"code":"DISK_USAGE_CHECK_UNAVAILABLE","component":"storage",
                       "severity":"WARNING","auto_repair":False})
    return issues[:MAX_ISSUES]


def recoverable(issues:list[dict],journal:dict,*,epoch:float)->tuple[list[str],dict]:
    """Two separate health probes and bounded rolling restart budget."""
    old=journal.get("incidents",{}) if isinstance(journal.get("incidents"),dict) else {}
    history=journal.get("restart_history",{}) if isinstance(journal.get("restart_history"),dict) else {}
    actions=[]
    updated={}
    for issue in issues:
        key=issue["component"]+":"+issue["code"]
        prev=old.get(key,{})
        if not isinstance(prev,dict):
            prev={}
        last=float(prev.get("last_probe_epoch") or 0)
        first=float(prev.get("first_probe_epoch") or epoch)
        count=int(prev.get("checks",0))
        if last and MIN_CONFIRMATION_GAP_SECONDS<=epoch-last<=10*60:
            count+=1
        elif not last or epoch-last>10*60:
            count=1
        updated[key]={"first_probe_epoch":first,
                      "last_probe_epoch":epoch,"checks":count}
        comp=issue["component"]
        if issue.get("auto_repair") and count>=PERSISTENT_INCIDENT_CONFIRMATIONS:
            prior=[float(t) for t in history.get(comp,[]) if
                   isinstance(t,(int,float)) and 0<=epoch-float(t)<=RESTART_WINDOW_SECONDS]
            if (len(prior)<MAX_RESTARTS_PER_COMPONENT_PER_WINDOW
                and (not prior or epoch-prior[-1]>=RESTART_COOLDOWN_SECONDS)):
                actions.append(comp)
                history[comp]=prior+[epoch]
                # Once a repair is attempted, issue needs a NEW confirmation.
                updated.pop(key,None)
    # Remove resolved incident counters on every healthy check.
    return actions,{
        "incidents":updated,
        "restart_history":{
            k:[t for t in v if isinstance(t,(int,float))
               and 0<=epoch-t<=RESTART_WINDOW_SECONDS]
            for k,v in history.items()
            if isinstance(v,list)
        },
    }


def optional_ai_advice(issues:list[dict],journal:dict,now:datetime)->dict:
    """Opt-in model *advice only*. Never sends tokens/stock data or enacts output.

    ChatGPT subscription is NOT an API key; explicit user-provided API
    credentials and NSE_AI_DOCTOR_ENABLE=1 required. One input per novel
    incident fingerprint per 6h to bound cost. Errors fail closed.
    """
    if not issues:
        return {"state":"NO_AI_REVIEW_NEEDED"}
    if os.environ.get("NSE_AI_DOCTOR_ENABLE")!="1":
        return {"state":"EXTERNAL_AI_NOT_ENABLED_RULE_BASED_RECOVERY_RUNNING"}
    token=os.environ.get("OPENAI_API_KEY","")
    if not token:
        return {"state":"OPENAI_API_KEY_MISSING"}
    issue_codes=[{"component":x["component"],"code":x["code"],
                  "auto_repair":bool(x["auto_repair"])} for x in issues]
    fingerprint=hashlib.sha256(json.dumps(issue_codes,sort_keys=True).encode()).hexdigest()
    epoch=now.timestamp()
    ai=journal.get("ai",{}) if isinstance(journal.get("ai"),dict) else {}
    previous_ai=epoch-float(ai.get("last_attempt",0))
    if (previous_ai<60*60 or
        (ai.get("fingerprint")==fingerprint and previous_ai<6*3600)):
        return {"state":"AI_DIAGNOSIS_RATE_LIMITED"}
    # Record attempt BEFORE network, to avoid retry storm on API failure.
    journal["ai"]={"fingerprint":fingerprint,"last_attempt":epoch}
    try:
        response=requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"},
            json={
                "model":os.environ.get("NSE_AI_DOCTOR_MODEL","gpt-5-mini"),
                "store":False,"max_output_tokens":200,
                "instructions":(
                    "Analyze only sanitized NSE data-research system issue CODES. "
                    "Provide one concise operator next step. Don't suggest trading, "
                    "bypassing access controls, deleting data, running shell commands "
                    "from model output, changing secrets or executing broker orders."
                ),
                "input":json.dumps(issue_codes),
            },timeout=(4,12),allow_redirects=False,
        )
        if response.status_code!=200:
            return {"state":"AI_ADVISOR_HTTP_ERROR"}
        body=response.json()
        if body.get("status")!="completed":
            return {"state":"AI_ADVISOR_INCOMPLETE"}
        messages=[]
        for item in body.get("output",[])[:3]:
            if item.get("type")=="message":
                for part in item.get("content",[])[:3]:
                    if part.get("type")=="output_text":
                        messages.append(str(part.get("text",""))[:300])
        text=" ".join(messages).strip()[:400]
        if not text:
            return {"state":"AI_ADVISOR_NO_TEXT"}
        return {"state":"AI_DIAGNOSIS_ADVICE_ONLY","advice":text,
                "permission_to_execute_model_instructions":False}
    except (requests.RequestException,ValueError,TypeError,KeyError):
        return {"state":"AI_ADVISOR_UNAVAILABLE"}


def evaluate(root,children,births,*,now=None,epoch=None,probe=None,
             use_optional_ai=True)->tuple[dict,list[str]]:
    """Save journal and health report before supervisor terminates any child."""
    root=Path(root)
    now=now or datetime.now(timezone.utc)
    epoch=epoch if epoch is not None else now.timestamp()
    issues=inspect(root,children,births,now=now,epoch=epoch,probe=probe)
    journal=read_json(root/JOURNAL)
    actions,next_journal=recoverable(issues,journal,epoch=epoch)
    next_journal["ai"]=journal.get("ai",{})
    advice=(optional_ai_advice(issues,next_journal,now)
            if use_optional_ai else {"state":"AI_REVIEW_NOT_REQUESTED_IN_TEST"})
    atomic_status(root/JOURNAL,next_journal)
    report={
        "checked_at_utc":now.astimezone(timezone.utc).isoformat(),
        "state":"AUTO_DOCTOR_ISSUES_DETECTED" if issues else "AUTO_DOCTOR_HEALTHY",
        "issue_count":len(issues),"issues":issues,
        "automatic_recovery_requested":actions,
        "safe_restart_cap_per_component_per_6h":MAX_RESTARTS_PER_COMPONENT_PER_WINDOW,
        "uncorrectable_issues_require_operator_or_code_review":True,
        "optional_ai_diagnosis":advice,
        "entire_project_repaired":False,
        "financial_source_files_modified":False,
        "orders":"DISABLED",
    }
    atomic_status(root/STATUS,report)
    return report,actions
