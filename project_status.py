"""Compact local project status from already-generated evidence only.

No network calls, broker calls, SMS, or orders are made here.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from research_monitor.paths import data_root


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def csv_rows(path: Path) -> list[dict]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))
    except OSError:
        return []


def truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "pass", "ok"}


def int_value(value, default=0) -> int:
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return default


def snapshot(root: Path | None = None) -> dict:
    root = Path(root or data_root())
    runtime = root / "runtime"

    history = read_json(runtime / "nse_eod_history_status.json")
    daily = history.get("daily") or {}
    gap = history.get("archive_gap_audit") or {}
    action = history.get("corporate_action_audit") or {}
    worker = read_json(runtime / "worker_status.json")
    hist_worker = read_json(runtime / "history_worker_status.json")
    post = read_json(runtime / "history_backtest_done.json")
    paper = read_json(runtime / "paper_status.json")

    annual_rows = csv_rows(root / "annual_fundamental_quality_summary.csv")
    annual = next(
        (row for row in annual_rows if row.get("Financial_Year") == "FUNDAMENTAL_SCREEN"),
        {},
    )
    final_rows = csv_rows(root / "final_fundamental_screen_summary.csv")
    final = final_rows[-1] if final_rows else {}
    quarterly_rows = csv_rows(root / "quarterly_collection_status.csv")
    quarterly = quarterly_rows[-1] if quarterly_rows else {}

    sectors = csv_rows(root / "nse_sector_map.csv")
    sector_ok = sum(row.get("Lookup_Status") == "OK" for row in sectors)

    backtest = csv_rows(root / "swing_backtest_report.csv")
    technical_pass = sum(truthy(row.get("Backtest_Pass")) for row in backtest)
    candidates = csv_rows(root / "swing_trade_candidates.csv")

    history_published = bool((history.get("publish") or {}).get("published"))
    remaining = int_value(
        daily.get("remaining_archive_days", daily.get("remaining_weekdays", 0))
    )
    unresolved_gaps = int_value(
        gap.get("unverified_expected_session_gaps", daily.get("unverified_no_file_days", 0))
    )
    action_review_symbols = int_value(action.get("unsupported_symbols", 0))
    full_pass = int_value(annual.get("Year_Data_Quality_Pass", 0))
    final_count = int_value(final.get("Final_Shortlist_Count", 0))

    if not history_published:
        stage = "HISTORY_CATCHUP"
    elif post.get("state") != "BACKTEST_SCREEN_PAPER_READINESS_COMPLETE":
        stage = "POST_HISTORY_VALIDATION"
    elif final_count == 0:
        stage = "RESEARCH_READY_NO_STRICT_FUNDAMENTAL_CANDIDATES"
    elif not candidates:
        stage = "RESEARCH_READY_NO_CURRENT_TECHNICAL_SIGNAL"
    else:
        stage = "CONDITIONAL_RESEARCH_CANDIDATES_AVAILABLE"

    return {
        "stage": stage,
        "backend_phase": worker.get("phase", "UNKNOWN"),
        "history_worker": hist_worker.get("state", "UNKNOWN"),
        "history_state": history.get("state", "NOT_STARTED"),
        "history_published": history_published,
        "remaining_archive_days": remaining,
        "unverified_archive_gaps": unresolved_gaps,
        "corporate_action_review_symbols": action_review_symbols,
        "annual_strict_pass": full_pass,
        "final_fundamental_shortlist": final_count,
        "quarterly_source_status": quarterly.get("Collection_Status", "MISSING"),
        "official_sector_rows": sector_ok,
        "technical_backtest_pass": technical_pass,
        "current_conditional_candidates": len(candidates),
        "post_history_validation": post.get("state", "NOT_STARTED"),
        "paper_status": paper.get("data_status", "NOT_STARTED"),
        "live_feed": worker.get("live_feed", "NOT_CONNECTED"),
        "sms": worker.get("sms", "PROVIDER_SETUP_REQUIRED"),
        "upstox": "INTENTIONALLY_DEFERRED",
        "orders": "MANUAL_ONLY",
    }


def main() -> None:
    print(json.dumps(snapshot(), indent=2))


if __name__ == "__main__":
    main()
