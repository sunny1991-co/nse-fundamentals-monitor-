"""Compact local project status from already-generated evidence only.

No network calls, broker calls, SMS, or orders are made here.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from research_monitor.paths import data_root, CODE_ROOT
from research_monitor.forward_status import summary as forward_summary


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



def seven_strategy_snapshot(root: Path, runtime: Path) -> dict:
    """Only show existing private research when code and input digests match."""
    result = {
        "seven_strategy_research": "NOT_RUN",
        "seven_strategy_tested": 0,
        "seven_strategy_research_top_three": [],
        "seven_strategy_verified_top_three": [],
    }
    experiment = read_json(runtime / "experiments" / "seven-strategies-v1" / "comparison.json")
    if not experiment:
        return result
    result["seven_strategy_research"] = "STALE_OR_UNVERIFIED_RESEARCH_REPORT"
    metadata = read_json(root / "market_data_metadata.json")
    code_paths = (
        "swing_seven_strategy_research.py",
        "research_monitor/seven_strategy_rules.py",
        "research_monitor/strategy_sources.py",
        "research_monitor/exploratory_ranking.py",
        "swing_backtest.py", "swing_strategy.py", "swing_pullback_research.py",
    )
    try:
        code_digest = hashlib.sha256(b"".join(
            (CODE_ROOT / name).read_bytes() for name in code_paths
        )).hexdigest()
    except OSError:
        return result
    if not (
        metadata.get("sha256")
        and experiment.get("data_sha256") == metadata.get("sha256")
        and experiment.get("implementation_sha256") == code_digest
        and experiment.get("scope") == "SEVEN_HYPOTHESES_EXPLORATORY_ONLY"
        and experiment.get("validation_status") == "NOT_VALIDATED"
        and experiment.get("live_eligible") is False
        and experiment.get("verified_top_three") == []
    ):
        return result
    csv_names = {"benchmark": "nifty50_tri_history.csv",
                 "earnings": "official_earnings_events.csv"}
    meta_names = {"benchmark": "nifty50_tri_history_metadata.json",
                  "earnings": "official_earnings_events_metadata.json"}
    source_hashes = experiment.get("source_hashes") or {}
    meta_hashes = experiment.get("metadata_hashes") or {}
    for kind in csv_names:
        src, meta = root / csv_names[kind], root / meta_names[kind]
        if kind in source_hashes:
            try:
                if (hashlib.sha256(src.read_bytes()).hexdigest() != source_hashes[kind]
                        or hashlib.sha256(meta.read_bytes()).hexdigest()
                        != meta_hashes.get(kind)):
                    return result
            except OSError:
                return result
        elif src.exists() or meta.exists():
            return result
    summary = experiment.get("summary") or {}
    research_top = experiment.get("research_top_three_unvalidated") or []
    if (not isinstance(summary, dict) or not isinstance(research_top, list)
            or len(research_top) > 3):
        return result
    result.update(
        seven_strategy_research="CURRENT_EXPLORATORY_NOT_VALIDATED",
        seven_strategy_tested=len(summary),
        seven_strategy_research_top_three=research_top,
    )
    return result




def custom_strategy_snapshot(root: Path, runtime: Path) -> dict:
    """Local status only; stale custom research cannot imply profitable trading."""
    result = {
        "custom_strategy_research": "NOT_RUN",
        "custom_strategy_name": "custom-dip-reclaim-v1",
        "custom_strategy_completed_trades": None,
        "custom_strategy_mean_completed_net_return": None,
        "custom_strategy_research_leader": None,
        "custom_strategy_live_eligible": False,
    }
    report = read_json(
        runtime / "experiments" / "custom-dip-reclaim-research-v1" / "comparison.json"
    )
    if not report:
        return result
    result["custom_strategy_research"] = "STALE_OR_UNVERIFIED_RESEARCH_REPORT"
    digest = read_json(root / "market_data_metadata.json").get("sha256")
    paths = (
        "swing_custom_strategy_research.py", "swing_backtest.py",
        "swing_strategy.py", "swing_pullback_research.py",
        "research_monitor/exploratory_ranking.py",
    )
    try:
        current_code_hash = hashlib.sha256(b"".join(
            (CODE_ROOT / name).read_bytes() for name in paths
        )).hexdigest()
    except OSError:
        return result
    if not (
        digest and report.get("data_sha256") == digest
        and report.get("implementation_sha256") == current_code_hash
        and report.get("scope") == "CUSTOM_STRATEGY_EXPLORATORY_NOT_VALIDATED"
        and report.get("validation_status") == "NOT_VALIDATED"
        and report.get("live_eligible") is False
        and report.get("orders") == "DISABLED"
        and report.get("verified_profitable_strategies") == 0
    ):
        return result
    values = (report.get("summary") or {}).get("custom-dip-reclaim-v1") or {}
    selection = report.get("research_selection") or {}
    result.update(
        custom_strategy_research="CURRENT_EXPLORATORY_NOT_VALIDATED",
        custom_strategy_completed_trades=values.get("pooled_completed_trades"),
        custom_strategy_mean_completed_net_return=values.get(
            "pooled_completed_mean_net_return"
        ),
        custom_strategy_research_leader=selection.get("research_leader"),
    )
    return result



def snapshot(root: Path | None = None) -> dict:
    root = Path(root or data_root())
    runtime = root / "runtime"

    history = read_json(runtime / "nse_eod_history_status.json")
    daily = history.get("daily") or {}
    gap = history.get("archive_gap_audit") or {}
    action = history.get("corporate_action_audit") or {}
    worker = read_json(runtime / "worker_status.json")
    hist_worker = read_json(runtime / "history_worker_status.json")
    upstox_reference_worker = read_json(
        runtime / "upstox_reference_worker_status.json"
    )
    vendor_reference_batch = read_json(
        runtime / "experiments" / "upstox-nse-historical-reference-v1"
        / "upstox-reference-batches-v1" / "last_batch.json"
    )
    vendor_quality = read_json(
        runtime / "experiments" / "upstox-nse-historical-reference-v1"
        / "upstox-reference-batches-v1" / "reconciliation" / "summary.json"
    )
    vendor_review_queue = read_json(
        runtime / "experiments" / "upstox-nse-historical-reference-v1"
        / "upstox-reference-batches-v1" / "reconciliation"
        / "company_source_review_summary.json"
    )
    post = read_json(runtime / "history_backtest_done.json")
    paper = read_json(runtime / "paper_status.json")
    experiment = read_json(
        runtime / "experiments" / "trend-pullback-research-v1" / "comparison.json"
    )
    research_state = "NOT_RUN"
    research_leader = None
    research_strategies = 0
    if experiment:
        # A previous valid-looking winner is never displayed after either data
        # or strategy code changes. No network, broker or raw-history read.
        metadata = read_json(root / "market_data_metadata.json")
        digest = metadata.get("sha256")
        try:
            code_hash = hashlib.sha256(b"".join(
                (CODE_ROOT / name).read_bytes() for name in (
                    "swing_pullback_research.py",
                    "swing_backtest.py",
                    "swing_strategy.py",
                )
            )).hexdigest()
        except OSError:
            code_hash = None
        if (digest and code_hash and experiment.get("data_sha256") == digest
                and experiment.get("implementation_sha256") == code_hash
                and experiment.get("live_eligible") is False
                and experiment.get("validation_status") == "NOT_VALIDATED"):
            research_state = "CURRENT_EXPLORATORY_NOT_VALIDATED"
            selection = experiment.get("research_selection") or {}
            research_leader = selection.get("research_leader")
            research_strategies = len(experiment.get("summary") or {})
        else:
            research_state = "STALE_OR_UNVERIFIED_RESEARCH_REPORT"

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
    fast_summary_rows = csv_rows(root / "fast_fundamental_screen_summary.csv")
    fast_summary = fast_summary_rows[-1] if fast_summary_rows else {}
    fast_candidates = csv_rows(root / "fast_swing_research_candidates.csv")

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
        "archive_gap_retry_exhausted": int_value(gap.get("retry_exhausted_gaps", 0)),
        "archive_gap_sample_dates": list(gap.get("sample_dates", []))[:10],
        "corporate_action_review_symbols": action_review_symbols,
        "annual_strict_pass": full_pass,
        "final_fundamental_shortlist": final_count,
        "quarterly_source_status": quarterly.get("Collection_Status", "MISSING"),
        "official_sector_rows": sector_ok,
        "technical_backtest_pass": technical_pass,
        "current_conditional_candidates": len(candidates),
        "provisional_fast_fundamental_pass": int_value(
            fast_summary.get("Fast_Fundamental_Pass_Count", 0)
        ),
        "provisional_fast_candidates": len(fast_candidates),
        "provisional_scope": (
            "LOWER_CONFIDENCE_RESEARCH_ONLY"
            if fast_summary else "NOT_RUN"
        ),
        "post_history_validation": post.get("state", "NOT_STARTED"),
        "paper_status": paper.get("data_status", "NOT_STARTED"),
        "automatic_forward_paper": forward_summary(root),
        "strategy_research": research_state,
        "exploratory_strategy_leader": research_leader,
        "research_strategies_compared": research_strategies,
        "verified_profitable_strategies": 0,
        "strategy_trading_enabled": False,
        **seven_strategy_snapshot(root, runtime),
        **custom_strategy_snapshot(root, runtime),
        "live_feed": worker.get("live_feed", "NOT_CONNECTED"),
        "sms": worker.get("sms", "PROVIDER_SETUP_REQUIRED"),
        # Offline status from the supervised worker only. Configured != valid
        # permission, current quote, or eligible-to-trade strategy.
        "broker_market_data_priority": ["GROWW", "UPSTOX"],
        "groww_token_status": (
            "CONFIGURED_NOT_LIVE_VERIFIED"
            if worker.get("groww_api_token_configured") is True
            else "NOT_CONFIGURED_OR_WORKER_NOT_UPDATED"
        ),
        "upstox_token_status": (
            "CONFIGURED_READ_ONLY_NOT_LIVE_VERIFIED"
            if worker.get("upstox_analytics_token_configured") is True
            else "NOT_CONFIGURED_OR_WORKER_NOT_UPDATED"
        ),
        "upstox_vendor_reference_daily_worker": upstox_reference_worker.get(
            "state", "NOT_STARTED"
        ),
        "upstox_vendor_reference_mapped_symbols": int_value(
            vendor_reference_batch.get("mapping_symbols", 0)
        ),
        "upstox_vendor_reference_completed": int_value(
            vendor_reference_batch.get("completed_company_records", 0)
        ),
        "upstox_vendor_reference_remaining": int_value(
            vendor_reference_batch.get("remaining_uncollected", 0)
        ),
        "upstox_vendor_reference_not_audited": True,
        "upstox_vs_verified_nse_comparisons": int_value(
            vendor_quality.get("metric_comparisons", 0)
        ) if vendor_quality.get("scope")==(
            "NSE_XBRL_VS_UPSTOX_REFERENCE_RECONCILIATION_V1"
        ) else 0,
        "upstox_vs_nse_companies_needing_source_review": int_value(
            vendor_quality.get("companies_needing_vendor_vs_nse_review", 0)
        ) if vendor_quality.get("scope")==(
            "NSE_XBRL_VS_UPSTOX_REFERENCE_RECONCILIATION_V1"
        ) else 0,
        "upstox_reconciliation_is_offline_reference_only": True,
        "upstox_source_review_worker_state": upstox_reference_worker.get(
            "source_review_queue_state","NOT_YET_RUN"
        ),
        "upstox_source_review_queue_companies": int_value(
            vendor_review_queue.get("companies_requiring_source_review",0)
        ) if vendor_review_queue.get("scope")==(
            "UPSTOX_NSE_FINANCIAL_SOURCE_REVIEW_QUEUE_V1"
        ) else 0,
        "upstox_source_review_queue_discrepancies": int_value(
            vendor_review_queue.get("mismatched_financial_metrics",0)
        ) if vendor_review_queue.get("scope")==(
            "UPSTOX_NSE_FINANCIAL_SOURCE_REVIEW_QUEUE_V1"
        ) else 0,
        "upstox": "BACKUP_ONLY_NSE_HISTORY_COLLECTOR_DOES_NOT_USE_UPSTOX",
        "automatic_groww_order_execution": "DISABLED_NO_VALIDATED_STRATEGY",
        "orders": "MANUAL_ONLY",
    }


def main() -> None:
    print(json.dumps(snapshot(), indent=2))


if __name__ == "__main__":
    main()
