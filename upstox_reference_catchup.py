"""Optional fast MANUAL vendor-reference catch-up; never changes daily cap.

Runs at most four separate 25-company batches (100 new attempts total)
SEQUENTIALLY with a conservative rest between batches. A 403/429, HTTP 5xx,
network/auth problem, invalid source map or unsafe record halts the session.
No concurrent requests, no automatic looping forever, no broker orders.
Existing FY2022-26 data and strict fundamental pass rules are NOT modified.

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_catchup.py

Only start manually after reading provider API limits. Safe to stop with
Ctrl+C: per-company records are checkpointed atomically, and resumed later.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import time

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status
from upstox_reference_batches import (
    ReferenceBatchError, MAX_BATCH, _stored_paths, collect,
)

MAX_MANUAL_ATTEMPTS = 100
INTER_BATCH_REST_SECONDS = 10
SOURCE = "UPSTOX_NSE_EQ_MANUAL_BOUNDED_CATCHUP_REFERENCE_ONLY"


def catch_up(root, token, *, max_attempts=MAX_MANUAL_ATTEMPTS,
             collector=collect, rest=time.sleep):
    if (
        isinstance(max_attempts,bool)
        or not isinstance(max_attempts,int)
        or not 1<=max_attempts<=MAX_MANUAL_ATTEMPTS
    ):
        raise ValueError("MAX_MANUAL_COMPANIES_MUST_BE_1_TO_100")
    if not isinstance(token,str) or not token.strip():
        raise ReferenceBatchError("UPSTOX_ANALYTICS_TOKEN_MISSING")
    tried=0
    acquired=0
    requests=0
    batches=0
    state="SESSION_NO_ATTEMPTS"
    last={}
    statuses=[]
    while tried<max_attempts:
        next_batch=min(MAX_BATCH,max_attempts-tried)
        try:
            result=collector(root,token,next_batch)
        except ReferenceBatchError as exc:
            # The underlying collector raises fixed tokens, no secrets or
            # user-entered HTTP responses. Do not echo text here regardless.
            state="STOPPED_ON_REFERENCE_OR_MAPPING_GUARD"
            statuses.append({"state":state})
            break
        attempted=result.get("batch_attempted_companies")
        done=result.get("new_company_records")
        calls=result.get("requests_this_run")
        if (
            result.get("scope")!=
            "NSE_EQ_UPSTOX_SECONDARY_VENDOR_INCREMENTAL_REFERENCE_ONLY"
            or any(
                isinstance(value,bool) or not isinstance(value,int)
                or value<0 for value in (attempted,done,calls)
            )
            or attempted>next_batch or done>attempted or calls>4*attempted
            or result.get("live_orders")!="DISABLED"
            or result.get("strict_fundamental_pass_changes")!=0
        ):
            state="STOPPED_ON_UNTRUSTED_BATCH_RESULT"
            statuses.append({"state":state})
            break
        batches+=1
        tried+=attempted
        acquired+=done
        requests+=calls
        last={
            "mapping_symbols":result.get("mapping_symbols"),
            "completed_company_records":result.get("completed_company_records"),
            "remaining_uncollected":result.get("remaining_uncollected"),
        }
        reason=result.get("stop_reason")
        statuses.append({
            "batch_number":batches,
            "attempted":attempted,
            "new_records":done,
            "requests":calls,
            "provider_stop_code":reason,
        })
        if reason:
            state="STOPPED_ON_PROVIDER_LIMIT_OR_NETWORK_CONSTRAINT"
            break
        if attempted==0:
            state="NO_UNCOLLECTED_ELIGIBLE_COMPANIES"
            break
        if attempted>0 and done==0:
            # Avoid hammering a provider returning no usable statements.
            state="STOPPED_ON_ZERO_SUCCESS_SOURCE_REVIEW_REQUIRED"
            break
        if result.get("remaining_uncollected")==0:
            state="ALL_CURRENT_MAPPED_VENDOR_REFERENCES_COLLECTED"
            break
        state="BOUNDED_CATCHUP_COMPLETE"
        if tried<max_attempts:
            rest(INTER_BATCH_REST_SECONDS)

    report={
        "scope":SOURCE,
        "created_at_utc":datetime.now(timezone.utc).isoformat(),
        "maximum_new_company_attempts":MAX_MANUAL_ATTEMPTS,
        "requested_cap":max_attempts,
        "companies_attempted":tried,
        "new_company_records":acquired,
        "requests_this_manual_session":requests,
        "completed_batches":batches,
        **last,
        "state":state,
        "batch_summaries":statuses,
        "auto_daily_limit_unchanged":25,
        "official_nse_verified_years_added":0,
        "official_nse_sector_rows_added":0,
        "strict_fundamental_pass_changes":0,
        "trade_candidate_changes":0,
        "source_kind":"UPSTOX_VENDOR_REFERENCE_NOT_NSE_AUDITED",
        "orders":"DISABLED",
    }
    folder=_stored_paths(root)[0]
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    destination=folder/"last_manual_catchup.json"
    atomic_status(destination,report)
    os.chmod(destination,0o600)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies",type=int,default=100,
                        help="Maximum NEW company attempts, 1..100")
    args=parser.parse_args()
    if not 1<=args.companies<=100:
        parser.error("--companies must be between 1 and 100")
    token=os.environ.get("UPSTOX_ANALYTICS_TOKEN","")
    if not token:
        raise SystemExit("UPSTOX_ANALYTICS_TOKEN_NOT_CONFIGURED_IN_LOCAL_ENV")
    try:
        result=catch_up(data_root(),token,max_attempts=args.companies)
    except (ReferenceBatchError,OSError,ValueError):
        raise SystemExit("MANUAL_REFERENCE_CATCHUP_FAILED_LOCAL_GUARD") from None
    print(json.dumps({
        k:result.get(k) for k in (
            "scope","companies_attempted","new_company_records",
            "requests_this_manual_session","completed_batches",
            "completed_company_records","remaining_uncollected",
            "state","auto_daily_limit_unchanged",
            "strict_fundamental_pass_changes","orders"
        )
    },indent=2))
    print("Private session report:",_stored_paths(data_root())[0]/
          "last_manual_catchup.json")


if __name__=="__main__":
    main()
