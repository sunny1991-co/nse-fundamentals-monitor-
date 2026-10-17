#!/usr/bin/env python3
"""Offline, daily evidence-gaps inventory for ALL official NSE EQ mainboard symbols.

Not a stock ranking, performance prediction, trade watchlist or authorization.
Never fetches broker data, modifies historical prices, or sends orders/SMS.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

from nse_mainboard_discovery import CATALOG, STATUS, write_csv_atomic
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

OUTPUT = "nse_mainboard_evidence_gaps.csv"
RESULT = "runtime/nse_mainboard_evidence_gaps_status.json"
MAX_AGE_HOURS = 48
MIN_UNIVERSE = 1000
SYMBOL = re.compile(r"^[A-Z0-9&._-]{1,40}$")
ISIN = re.compile(r"^IN[A-Z0-9]{10}$")
COLUMNS = [
    "Symbol", "ISIN", "Company", "Baseline_Tracked",
    "FY2024_25_Row_Present", "FY2025_26_Row_Present",
    "Strict_Annual_Pass", "Strict_Final_Fundamental_Pass",
    "Local_EOD_Completed_Bars_Unattested", "Local_EOD_Complex_Action_Flag",
    "Research_State", "Next_Data_Action", "Trade_Eligible",
]


class EvidenceGapError(RuntimeError):
    pass


def read_table(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise EvidenceGapError("LOCAL_CSV_INVALID_HEADERS:" + path.name)
            if path.stat().st_size > 40_000_000:
                raise EvidenceGapError("LOCAL_CSV_TOO_LARGE:" + path.name)
            return list(reader)
    except (UnicodeError, csv.Error, OSError):
        raise EvidenceGapError("LOCAL_CSV_UNREADABLE:" + path.name) from None


def symbol_set(path: Path) -> set[str]:
    return {sym for row in read_table(path)
            if (sym := str(row.get("Symbol") or row.get("SYMBOL") or "").strip().upper())
            and SYMBOL.fullmatch(sym)}


def flag_set(path: Path, field: str) -> set[str]:
    return {sym for row in read_table(path)
            if (sym := str(row.get("Symbol", "")).strip().upper())
            and SYMBOL.fullmatch(sym)
            and str(row.get(field, "")).strip().lower() in ("true", "yes", "1")}


def read_local_bars(path: Path) -> dict[str, dict]:
    result = {}
    for row in read_table(path):
        sym = str(row.get("Symbol", "")).strip().upper()
        if not SYMBOL.fullmatch(sym) or sym in result:
            raise EvidenceGapError("LOCAL_HISTORY_COVERAGE_DUPLICATE_OR_INVALID")
        count = str(row.get("Completed_Bars", "")).strip()
        if not count.isdigit() or int(count) > 1000000:
            raise EvidenceGapError("LOCAL_HISTORY_COVERAGE_INVALID_BARS")
        result[sym] = row
    return result


def snapshot(root: Path, *, now: datetime | None = None) -> dict:
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timezone-aware clock required")
    status_file = root / STATUS
    if not status_file.is_file():
        raise EvidenceGapError("OFFICIAL_DISCOVERY_NOT_YET_COMPLETED")
    try:
        source = json.loads(status_file.read_text(encoding="utf-8"))
        stamped = datetime.fromisoformat(source["checked_at_utc"])
        if stamped.tzinfo is None or not (
            timedelta(0) <= now - stamped.astimezone(timezone.utc)
            <= timedelta(hours=MAX_AGE_HOURS)
        ):
            raise EvidenceGapError("OFFICIAL_DISCOVERY_SNAPSHOT_STALE")
        if source.get("state") != "OFFICIAL_DISCOVERY_UPDATED":
            raise EvidenceGapError("OFFICIAL_DISCOVERY_NOT_VERIFIED")
        expected = source["mainboard_eq_count"]
        if not isinstance(expected, int) or expected < MIN_UNIVERSE:
            raise EvidenceGapError("OFFICIAL_DISCOVERY_BAD_COUNT")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise EvidenceGapError("OFFICIAL_DISCOVERY_STATUS_INVALID") from None

    catalog_path = root / CATALOG
    catalog = read_table(catalog_path)
    if len(catalog) != expected:
        raise EvidenceGapError("OFFICIAL_DISCOVERY_CATALOG_COUNT_MISMATCH")
    known_symbols, known_isins = set(), set()
    for row in catalog:
        sym = str(row.get("Symbol", "")).strip()
        isin = str(row.get("ISIN", "")).strip()
        if (not SYMBOL.fullmatch(sym) or not ISIN.fullmatch(isin)
                or sym in known_symbols or isin in known_isins
                or row.get("Series") != "EQ"):
            raise EvidenceGapError("OFFICIAL_DISCOVERY_CATALOG_CONFLICT")
        known_symbols.add(sym)
        known_isins.add(isin)

    fy24 = symbol_set(root / "FY2425.csv")
    fy25 = symbol_set(root / "NSE_FY2025-26_Consolidated_Fundamentals.csv")
    annual = flag_set(root / "annual_fundamental_quality_report.csv",
                      "Full_Fundamental_Criteria_Pass")
    final = symbol_set(root / "final_fundamental_shortlist.csv")
    # Historical coverage is shown only as a locally reported figure. It is
    # NOT checked against price payload and cannot authorize trading.
    bars = read_local_bars(root / "nse_eod_history_coverage.csv")

    output = []
    counts: dict[str, int] = {}
    for row in sorted(catalog, key=lambda x: x["Symbol"]):
        sym = row["Symbol"]
        in24, in25 = sym in fy24, sym in fy25
        annually = sym in annual
        qualified = annually and sym in final
        history = bars.get(sym, {})
        local_bars = history.get("Completed_Bars", "")
        complex_flag = str(history.get("Unsupported_Complex_Action", "")).lower()
        tracked = row.get("Baseline_Tracked") == "true"
        if not in24 or not in25:
            state = "NEEDS_AUDITED_ANNUAL_DATA"
            action = "ACQUIRE_VERIFY_BOTH_FY2024_25_AND_FY2025_26_ANNUAL_FILINGS"
        elif not annually:
            state = "STRICT_FUNDAMENTALS_NOT_PASSED"
            action = "VERIFY_FOUR_YEARS_CASH_FLOW_SECTOR_QUALITY"
        elif not qualified:
            state = "QUARTERLY_OR_FINAL_SCREEN_PENDING"
            action = "VERIFY_CURRENT_QUARTERS_AND_STRICT_FUNDAMENTALS"
        else:
            state = "EXISTING_FUNDAMENTAL_RESEARCH_ONLY"
            action = "VERIFY_INDEPENDENTLY_ADJUSTED_OHLCV_AND_OUT_OF_SAMPLE_BACKTEST"
        counts[state] = counts.get(state, 0) + 1
        output.append({
            "Symbol": sym, "ISIN": row["ISIN"], "Company": row["Company"],
            "Baseline_Tracked": "true" if tracked else "false",
            "FY2024_25_Row_Present": str(in24).lower(),
            "FY2025_26_Row_Present": str(in25).lower(),
            "Strict_Annual_Pass": str(annually).lower(),
            "Strict_Final_Fundamental_Pass": str(qualified).lower(),
            "Local_EOD_Completed_Bars_Unattested": local_bars,
            "Local_EOD_Complex_Action_Flag": complex_flag,
            "Research_State": state, "Next_Data_Action": action,
            "Trade_Eligible": "false",
        })
    # Source identifiers and count must remain stable while writing reports.
    payload = catalog_path.read_bytes()
    root.mkdir(parents=True, exist_ok=True)
    write_csv_atomic(root / OUTPUT, COLUMNS, output)
    report = {
        "checked_at_utc": now.astimezone(timezone.utc).isoformat(),
        "state": "RESEARCH_EVIDENCE_GAPS_UPDATED",
        "official_catalog_sha256": hashlib.sha256(payload).hexdigest(),
        "mainboard_companies": expected,
        "outside_annual_baseline": sum(r["Baseline_Tracked"] == "false" for r in output),
        "missing_at_least_one_fy_row": counts.get("NEEDS_AUDITED_ANNUAL_DATA", 0),
        "research_states": counts,
        "local_history_coverage_is_unattested": True,
        "new_companies_automatically_tradable": False,
        "orders": "DISABLED",
    }
    atomic_status(root / RESULT, report)
    return report


def main() -> int:
    import json as _json
    try:
        report = snapshot(data_root())
    except (EvidenceGapError, OSError, ValueError) as exc:
        report = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "state": "EVIDENCE_GAPS_BLOCKED", "reason": str(exc)[:160],
            "prior_report_preserved": True, "orders": "DISABLED",
        }
        atomic_status(data_root() / RESULT, report)
        print(_json.dumps(report, indent=2))
        return 1
    print(_json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
