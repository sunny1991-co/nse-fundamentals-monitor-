"""Read-only audit of extreme adjusted NSE price gaps against local raw archives.

Never excludes a stock, changes signals, triggers orders, or claims an archived
NSE digest independently verifies a source. Event-dated evidence for human
review; original strategy results remain unchanged.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

import pandas as pd

from research_monitor.history import read_history
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status
from swing_custom_strategy_research import END_DATE
from swing_nse_history import classify_action, intervals_cover

THRESHOLD = 0.35  # Frozen flag threshold; not a trading filter.
MIN_BARS = 1260
STUDY = "price-integrity-audit-v1"
SOURCE_SCOPE = "OFFICIAL_NSE_EOD_RESEARCH_NOT_LIVE_NOT_BROKER_DATA"
PRICE_FIELDS = ("Open", "High", "Low", "Close", "Volume")


def _day(value):
    return date.fromisoformat(str(value)[:10])


def sample_jumps(history, limit=100):
    """Use the same fixed 100-symbol hash-sample as previous losing research."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 5000:
        raise ValueError("LIMIT_MUST_BE_0_TO_5000")
    frozen = history.loc[history.Date <= pd.Timestamp(END_DATE)]
    groups = {sym: g.reset_index(drop=True) for sym, g in frozen.groupby("Symbol")
              if len(g) >= MIN_BARS}
    candidates = sorted(groups, key=lambda sym: hashlib.sha256(
        sym.encode("utf-8")).hexdigest())
    symbols = candidates[:limit] if limit else candidates
    events = []
    for sym in symbols:
        g = groups[sym]
        cutoff = 220 + int((len(g)-220)*.7)
        prev = g.Close.shift(1)
        gap = g.Open/prev-1
        close_move = g.Close/prev-1
        flagged = ((gap.abs() > THRESHOLD) | (close_move.abs() > THRESHOLD)).fillna(False)
        for index in g.index[flagged]:
            if index < 1:
                continue
            previous = g.iloc[index-1]
            today = g.iloc[index]
            events.append({
                "symbol": sym,
                "day": str(today.Date.date()),
                "prior_day": str(previous.Date.date()),
                "prev_adjusted_close": float(previous.Close),
                "adjusted_open": float(today.Open),
                "adjusted_close": float(today.Close),
                "open_gap_pct": round(float(100*gap.iloc[index]), 3),
                "close_move_pct": round(float(100*close_move.iloc[index]), 3),
                "in_frozen_test_window": bool(index >= cutoff),
                "review_classification": "PENDING_LOCAL_PROVENANCE_REVIEW",
            })
    events.sort(key=lambda e:(e["symbol"],e["day"]))
    return len(groups), symbols, events


def _connect_cache(root):
    """Open existing SQLite ONLY in immutable read-only mode, never create it."""
    path = Path(root)/"runtime"/"nse_eod_history.sqlite"
    if not path.is_file():
        return None, "LOCAL_NSE_CACHE_MISSING"
    uri = "file:" + quote(path.resolve().as_posix(), safe="/") + "?mode=ro&immutable=1"
    try:
        db = sqlite3.connect(uri, uri=True, timeout=10)
        db.execute("SELECT symbol,day,close,source_sha256 FROM bars LIMIT 1").fetchone()
        db.execute("SELECT symbol,ex_date,purpose FROM actions LIMIT 1").fetchone()
        db.execute("SELECT start_day,end_day,status FROM action_windows LIMIT 1").fetchone()
        return db, "LOCAL_NSE_CACHE_READ_ONLY"
    except sqlite3.Error:
        try:
            db.close()
        except (NameError, sqlite3.Error):
            pass
        return None, "LOCAL_NSE_CACHE_SCHEMA_UNAVAILABLE"


def _source_meta_range(metadata):
    if metadata.get("scope") != SOURCE_SCOPE:
        return None
    try:
        begin = _day(metadata["range_start"])
        end = _day(metadata["range_end"])
        if begin > end:
            return None
        return begin, end
    except (ValueError, KeyError, TypeError):
        return None


def _load_actions(db, symbol, meta_range):
    first, end = meta_range
    rows = db.execute(
        """SELECT ex_date,purpose,kind,factor,supported,source_hash
           FROM actions WHERE symbol=? AND ex_date BETWEEN ? AND ?
           ORDER BY ex_date,purpose""",
        (symbol, first.isoformat(), end.isoformat()),
    ).fetchall()
    actions = []
    for day,purpose,stored_kind,stored_factor,stored_supported,source_hash in rows:
        classified = classify_action(symbol,_day(day),purpose)
        cache_agrees = (
            classified.kind == stored_kind
            and classified.supported == bool(stored_supported)
            and (
                (classified.factor is None and stored_factor is None)
                or (classified.factor is not None and stored_factor is not None
                    and math.isclose(float(classified.factor),
                                     float(stored_factor),abs_tol=1e-10))
            )
        )
        actions.append({
            "ex_date": day,
            "purpose": purpose,
            "kind": classified.kind,
            "factor": classified.factor,
            "supported": classified.supported,
            "cache_classification_matches": cache_agrees,
            "source_hash_present": bool(
                re.fullmatch(r"[0-9a-fA-F]{64}",str(source_hash or ""))
            ),
        })
    return actions


def _cache_raw(db, symbol, day):
    row = db.execute(
        """SELECT open,high,low,close,volume,source_url,source_sha256
           FROM bars WHERE symbol=? AND day=?""",
        (symbol, day),
    ).fetchone()
    if row is None:
        return None
    fields = dict(zip(("Open","High","Low","Close","Volume",
                       "Source_URL","Source_SHA256"), row))
    status = db.execute(
        "SELECT status FROM days WHERE day=?", (day,)
    ).fetchone()
    fields["Archive_Day_OK"] = status is not None and status[0] == "OK"
    fields["Source_Hash_Present"] = bool(re.fullmatch(
        r"[0-9a-fA-F]{64}",str(fields["Source_SHA256"] or "")
    ))
    fields["Archive_URL_Present"] = str(fields["Source_URL"] or "").startswith(
        "https://nsearchives.nseindia.com/"
    )
    return fields


def _expected_values(raw, day, actions):
    if raw is None:
        return None, "RAW_BAR_MISSING"
    if any(not a["supported"] for a in actions):
        return None, "UNSUPPORTED_ACTION_IN_CACHED_RANGE"
    if any(not a["cache_classification_matches"] for a in actions):
        return None, "CACHED_ACTION_CLASSIFICATION_STALE"
    if any(not a["source_hash_present"] for a in actions):
        return None, "ACTION_SOURCE_DIGEST_MISSING"
    factor = 1.0
    for a in actions:
        if a["ex_date"] > day:
            factor *= float(a["factor"])
    if not math.isfinite(factor) or factor <= 0:
        return None, "INVALID_ADJUSTMENT_FACTOR"
    return {
        k: float(raw[k]) / factor if k != "Volume" else float(raw[k]) * factor
        for k in PRICE_FIELDS
    }, "EXPECTED_FROM_CACHED_RAW_AND_ACTIONS"


def _row_matches(expected, adjusted):
    if expected is None or adjusted is None:
        return False
    for key in PRICE_FIELDS:
        value = float(adjusted[key])
        target = float(expected[key])
        # Published history is rounded to six decimal places.
        if not math.isclose(value, target, rel_tol=1e-9, abs_tol=0.000002):
            return False
    return True


def _inspect_one(event, by_date, db, meta_range, actions, action_covered):
    day = event["day"]
    prior_day = event["prior_day"]
    relevant = [a for a in actions if
                abs((_day(a["ex_date"])-_day(day)).days) <= 7]
    event["nearby_cached_actions"] = [
        {k: a[k] for k in ("ex_date","kind","purpose","factor",
                          "supported","source_hash_present")}
        for a in relevant
    ]
    event["local_action_range_coverage"] = bool(action_covered)
    current_raw = _cache_raw(db, event["symbol"], day)
    previous_raw = _cache_raw(db, event["symbol"], prior_day)
    event["previous_raw_close"] = (
        float(previous_raw["Close"]) if previous_raw else None
    )
    event["current_raw_open"] = (
        float(current_raw["Open"]) if current_raw else None
    )
    event["current_raw_close"] = (
        float(current_raw["Close"]) if current_raw else None
    )
    event["cached_source_urls"] = [
        entry["Source_URL"] if entry else None
        for entry in (previous_raw, current_raw)
    ]
    event["cached_archive_sha256"] = [
        entry["Source_SHA256"] if entry else None
        for entry in (previous_raw,current_raw)
    ]
    if not action_covered:
        event["review_classification"] = "ACTION_WINDOW_NOT_FULLY_CACHED"
        return
    if not previous_raw or not current_raw:
        event["review_classification"] = "RAW_DAY_OR_PRIOR_DAY_MISSING"
        return
    if not all(
        x["Archive_Day_OK"] and x["Source_Hash_Present"]
        and x["Archive_URL_Present"]
        for x in (previous_raw, current_raw)
    ):
        event["review_classification"] = "RAW_CACHE_ARCHIVE_SOURCE_UNATTESTED"
        return
    before_expected, before_status = _expected_values(
        previous_raw,prior_day,actions
    )
    after_expected, after_status = _expected_values(current_raw,day,actions)
    if before_expected is None or after_expected is None:
        event["review_classification"] = (
            before_status if before_expected is None else after_status
        )
        return
    before_row = by_date.get(prior_day)
    after_row = by_date.get(day)
    if not _row_matches(before_expected,before_row):
        event["review_classification"] = "PREVIOUS_ADJUSTED_BAR_MISMATCH"
    elif not _row_matches(after_expected,after_row):
        event["review_classification"] = "JUMP_DAY_ADJUSTED_BAR_MISMATCH"
    else:
        event["review_classification"] = (
            "RECONCILES_WITH_CACHED_ARCHIVES_REVIEW_MARKET_EVENT"
        )


def audit(history, metadata, digest, root, limit=100, *, cache=None):
    """No network, no strategy selection and no exclusion of flagged symbols."""
    eligible, sample, events = sample_jumps(history,limit)
    range_info = _source_meta_range(metadata)
    cache_state = "LOCAL_CACHE_NOT_QUERIED"
    own_cache = False
    if cache is None:
        cache,cache_state = _connect_cache(root)
        own_cache = cache is not None
    else:
        cache_state = "LOCAL_CACHE_PROVIDED_FOR_TEST_OR_AUDIT"
    try:
        if range_info is None:
            cache_state = "PUBLISHED_NSE_METADATA_RANGE_NOT_ESTABLISHED"
            for e in events:
                e["review_classification"] = "SOURCE_METADATA_UNSUITABLE_FOR_RAW_REPLAY"
        elif cache is None:
            for e in events:
                e["review_classification"] = "RAW_NSE_CACHE_UNAVAILABLE"
        else:
            first,end = range_info
            windows = [
                (a,b) for a,b in cache.execute(
                    "SELECT start_day,end_day FROM action_windows WHERE status='OK'"
                ).fetchall()
            ]
            covered = intervals_cover(windows,first,end)
            for sym in sorted({x["symbol"] for x in events}):
                bars = history[history.Symbol == sym]
                by_date = {str(row.Date.date()): row for _,row in bars.iterrows()}
                actions = _load_actions(cache,sym,range_info)
                for event in (e for e in events if e["symbol"] == sym):
                    if not (first <= _day(event["prior_day"])
                            <= _day(event["day"]) <= end):
                        event["review_classification"] = "OUTSIDE_PUBLISHED_SOURCE_RANGE"
                        continue
                    _inspect_one(event,by_date,cache,range_info,actions,covered)
    finally:
        if own_cache:
            cache.close()
    from collections import Counter
    classifications = dict(sorted(Counter(
        e["review_classification"] for e in events
    ).items()))
    affected = {e["symbol"] for e in events}
    return {
        "scope": "NSE_PRICE_INTEGRITY_REVIEW_ONLY_NO_TRADING",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "history_data_sha256": digest,
        "frozen_research_through": END_DATE,
        "comparison_sample_order": "SHA256 eligible symbol, same first 100 as research",
        "eligible_history_universe": eligible,
        "sampled_stocks": len(sample),
        "sampled_symbol_sha256": hashlib.sha256(
            "\n".join(sample).encode()
        ).hexdigest(),
        "extreme_movement_threshold_abs_pct": 100*THRESHOLD,
        "flagged_symbols": len(affected),
        "extreme_jump_events": len(events),
        "events_inside_previously_inspected_test_window": sum(
            e["in_frozen_test_window"] for e in events
        ),
        "local_cache_status": cache_state,
        "cached_adjustment_reconciliation": classifications,
        "events": events,
        "research_strategy_approval": "DISABLED",
        "automatic_exclusions": 0,
        "live_eligible": False,
        "notes": [
            "A large jump can be a real price move, split, rights event or source problem.",
            "Cache reconciliation is NOT independent verification of NSE source truth.",
            "The same archived window was already inspected; do not optimize on flags.",
            "Action coverage is measured across the entire published source range.",
            "No strategy signals, frozen backtest result or approved universe changed.",
        ],
    }


CSV_FIELDS = [
    "symbol","prior_day","day","open_gap_pct","close_move_pct",
    "in_frozen_test_window","previous_raw_close","current_raw_open",
    "current_raw_close","review_classification",
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit",type=int,default=100,
                    help="First SHA256-ranked eligible symbols; 0 means entire eligible universe")
    ap.add_argument("--top",type=int,default=12,
                    help="Preview at most 1..30 individual flagged jump dates")
    args=ap.parse_args()
    if not 0 <= args.limit <= 5000 or not 1 <= args.top <= 30:
        ap.error("--limit 0..5000 and --top 1..30")
    root=data_root()
    hist,digest=read_history(root)
    meta=json.loads((root/"market_data_metadata.json").read_text())
    result=audit(hist,meta,digest,root,args.limit)
    folder=root/"runtime"/"experiments"/STUDY
    folder.mkdir(parents=True,exist_ok=True)
    atomic_status(folder/"report.json",result)
    csv_path=folder/"jump_events.csv"
    import os
    with (folder/"jump_events.tmp").open("w",newline="",encoding="utf-8") as out:
        writer=csv.DictWriter(out,fieldnames=CSV_FIELDS)
        writer.writeheader()
        for e in result["events"]:
            writer.writerow({field:e.get(field) for field in CSV_FIELDS})
    os.replace(folder/"jump_events.tmp",csv_path)
    review = sorted(
        result["events"],
        key=lambda e:(-max(abs(e["open_gap_pct"]),abs(e["close_move_pct"])),
                      e["symbol"],e["day"]),
    )[:args.top]
    print(json.dumps({
        key:result[key] for key in (
            "scope","eligible_history_universe","sampled_stocks",
            "flagged_symbols","extreme_jump_events",
            "events_inside_previously_inspected_test_window",
            "local_cache_status","cached_adjustment_reconciliation",
            "automatic_exclusions","live_eligible",
        )
    } | {"event_preview": [
        {key:e.get(key) for key in
         ("symbol","day","open_gap_pct","close_move_pct",
          "in_frozen_test_window","review_classification",
          "nearby_cached_actions")}
        for e in review
    ]},indent=2))
    print("Private full audit:",folder/"report.json")
    print("Private jump events CSV:",csv_path)


if __name__ == "__main__":
    main()
