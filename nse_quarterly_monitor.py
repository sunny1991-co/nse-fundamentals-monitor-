#!/usr/bin/env python3
"""Strict NSE quarterly fundamentals monitor.

Only XBRL facts whose context explicitly identifies consolidated statements are
compared. Missing facts remain blank; unknown reporting basis is never guessed.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
API_URL = "https://www.nseindia.com/api/integrated-filing-results"
LANDING_URL = "https://www.nseindia.com/companies-listing/corporate-integrated-filing"
PAGE_SIZE = 100
MAX_PAGES = 300
LOOKBACK_DAYS = 1900
MAX_NEW_XBRL_PER_RUN = 3000
PARSER_VERSION = "2"
TARGET_BASIS = "Consolidated"
ANNUAL_QUALITY_FILE = ROOT / "annual_fundamental_quality_report.csv"

RESULTS_FILE = ROOT / "quarterly_results.csv"
COMPARE_FILE = ROOT / "quarterly_change_report.csv"
UNVERIFIED_FILE = ROOT / "quarterly_unverified_filings.csv"
PROCESSED_FILE = ROOT / "quarterly_processed_filings.csv"

VALUE_FIELDS = [
    "Revenue_Operations_Cr", "Net_Profit_Cr", "Basic_EPS_Rs",
    "Total_Assets_Cr", "Total_Equity_Cr", "Total_Borrowings_Cr",
]
METRICS = {
    "Revenue": "Revenue_Operations_Cr",
    "Net_Profit": "Net_Profit_Cr",
    "EPS": "Basic_EPS_Rs",
}
ALIASES = {
    "Revenue_Operations_Cr": ["RevenueFromOperations", "RevenueFromOperationsNetOfExciseDuty"],
    "Net_Profit_Cr": ["ProfitLossForPeriod", "ProfitLoss"],
    "Basic_EPS_Rs": ["BasicEarningsLossPerShare", "EarningsPerShareBasic", "BasicEPS"],
    "Total_Assets_Cr": ["Assets", "TotalAssets"],
    "Total_Equity_Cr": ["Equity", "TotalEquity"],
}
CURRENT_BORROWING_ALIASES = ["BorrowingsCurrent", "CurrentBorrowings", "BorrowingsCurrentPortion"]
NONCURRENT_BORROWING_ALIASES = ["BorrowingsNoncurrent", "NoncurrentBorrowings", "BorrowingsNonCurrent"]


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def parse_date(value: Any) -> date | None:
    if value is None or str(value).strip() in ("", "nan", "NaT", "None"):
        return None
    dt = pd.to_datetime(str(value).strip(), errors="coerce", dayfirst=True)
    if pd.isna(dt):
        return None
    return dt.date()


def xbrl_upload_date(url: str) -> date | None:
    match = re.search(r"_(\d{14})_WEB\.xml(?:$|\?)", url, re.I)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d%m%Y%H%M%S").date()
    except ValueError:
        return None


def first_value(record: dict, *names: str) -> Any:
    lowered = {str(k).lower(): v for k, v in record.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def source_url(record: dict) -> str:
    value = first_value(record, "xbrl", "xbrlUrl", "xbrl_file", "xbrlFile")
    return str(value).strip() if value else ""


def record_symbol(record: dict) -> str:
    return str(first_value(record, "symbol", "sm_symbol", "securitySymbol") or "").strip().upper()


def record_quarter_end(record: dict) -> date | None:
    return parse_date(first_value(record, "qe_Date", "quarterEndDate", "periodEndDate"))


def record_publish_date(record: dict) -> date | None:
    return (
        parse_date(first_value(record, "revised_Date", "filingDate", "submissionDate", "createdAt"))
        or xbrl_upload_date(source_url(record))
        or record_quarter_end(record)
    )


def json_items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "results", "records", "result", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            nested = json_items(value)
            if nested:
                return nested
    return []


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": LANDING_URL,
        "X-Requested-With": "XMLHttpRequest",
    })
    response = session.get(LANDING_URL, timeout=30)
    response.raise_for_status()
    return session


def get_api_page(session: requests.Session, page: int) -> tuple[list[dict], dict]:
    params = {"type": "Integrated Filing- Financials", "index": "equities", "page": page, "size": PAGE_SIZE}
    last_error = None
    for attempt in range(4):
        try:
            response = session.get(API_URL, params=params, timeout=35)
            if response.status_code in (403, 429, 500, 502, 503, 504):
                raise requests.HTTPError(f"NSE API HTTP {response.status_code}")
            response.raise_for_status()
            payload = response.json()
            return json_items(payload), payload if isinstance(payload, dict) else {}
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"NSE API page {page} failed: {last_error}")


def fetch_recent_records(session: requests.Session) -> list[dict]:
    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    all_records: list[dict] = []
    print(f"Fetching NSE integrated-financial filing index; cutoff={cutoff}")
    for page in range(MAX_PAGES):
        items, _payload = get_api_page(session, page)
        if not items:
            break
        all_records.extend(items)
        known_dates = [record_publish_date(x) for x in items]
        known_dates = [d for d in known_dates if d is not None]
        if len(items) < PAGE_SIZE:
            break
        # NSE's index is newest-first. Stop only when the whole page is old.
        if len(known_dates) >= max(10, int(0.8 * len(items))) and max(known_dates) < cutoff:
            print(f"Reached older filings at page {page}; stopping pagination.")
            break
        time.sleep(0.12)
    print(f"Indexed {len(all_records)} API records.")
    return all_records


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception as exc:
        raise RuntimeError(f"Cannot read existing output {path.name}: {exc}") from exc


def parse_contexts(root: ET.Element) -> dict[str, dict]:
    contexts: dict[str, dict] = {}
    for node in root.iter():
        if local_name(node.tag).lower() != "context":
            continue
        context_id = node.attrib.get("id")
        if not context_id:
            continue
        period_node = next((x for x in node.iter() if local_name(x.tag).lower() == "period"), None)
        if period_node is None:
            continue
        instant = None
        start = end = None
        for child in period_node:
            tag = local_name(child.tag).lower()
            if tag == "instant":
                instant = parse_date(child.text)
            elif tag == "startdate":
                start = parse_date(child.text)
            elif tag == "enddate":
                end = parse_date(child.text)
        members: list[tuple[str, str]] = []
        for child in node.iter():
            if local_name(child.tag).lower() == "explicitmember":
                axis = str(child.attrib.get("dimension", "")).split(":")[-1].lower()
                member = str(child.text or "").split(":")[-1].lower()
                members.append((axis, member))
        basis_hits = set()
        for axis, member in members:
            combined = axis + " " + member
            if "consolidated" in combined and "separate" not in combined and "standalone" not in combined:
                basis_hits.add("Consolidated")
            if "standalone" in combined or "separate" in member:
                basis_hits.add("Standalone")
        basis = next(iter(basis_hits)) if len(basis_hits) == 1 else ("MIXED" if len(basis_hits) > 1 else "UNVERIFIED")
        other_dimensions = []
        for axis, member in members:
            is_basis_member = (
                "consolidated" in member or "standalone" in member
                or "separate" in member
                or "consolidated" in axis
                or "typeofstatement" in axis
                or "financialstatement" in axis
            )
            if not is_basis_member:
                other_dimensions.append((axis, member))
        contexts[context_id] = {
            "instant": instant, "start": start, "end": end,
            "basis": basis, "members": members,
            "other_dimensions": other_dimensions,
        }
    return contexts


def get_fact(root: ET.Element, aliases: list[str], context_ids: set[str]) -> float | None:
    if not context_ids:
        return None
    wanted = {name.lower() for name in aliases}
    candidates: list[tuple[int, float]] = []
    rank = {name.lower(): i for i, name in enumerate(aliases)}
    for node in root.iter():
        name = local_name(node.tag).lower()
        if name not in wanted or node.attrib.get("contextRef") not in context_ids:
            continue
        raw = (node.text or "").strip()
        if not raw or node.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
            continue
        raw = raw.replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if pd.notna(value):
            candidates.append((rank[name], value))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]



def normalize_basis(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"consolidated", "consolidated financial statements"}:
        return "Consolidated"
    if text in {"standalone", "separate", "separate financial statements"}:
        return "Standalone"
    return "UNVERIFIED"


def xbrl_report_basis(root: ET.Element) -> set[str]:
    # NSE's XBRL uses this fact; the filing index separately exposes
    # the "consolidated" column. Require the two independent values to agree.
    basis_tag_names = {
        "natureofreportstandaloneconsolidated",
        "natureofreport",
        "natureofstatement",
        "natureoffinancialstatements",
    }
    found: set[str] = set()
    for node in root.iter():
        name = local_name(node.tag).lower()
        if name not in basis_tag_names:
            continue
        value = (node.text or "").strip().lower()
        if value == "consolidated":
            found.add("Consolidated")
        elif value in {"standalone", "separate"}:
            found.add("Standalone")
    return found


def parse_xbrl(xml_bytes: bytes, record: dict, url: str) -> tuple[dict | None, str]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return None, f"Invalid XBRL XML: {exc}"
    contexts = parse_contexts(root)
    metadata_basis = normalize_basis(first_value(
        record, "consolidated", "consolidatedStandalone", "natureOfReport",
        "nature_Of_Report", "reportingBasis"
    ))
    fact_basis = xbrl_report_basis(root)
    if metadata_basis == "UNVERIFIED":
        return None, "NSE filing index has no recognized Consolidated/Standalone label"
    if len(fact_basis) != 1:
        return None, "XBRL reporting-basis fact missing or conflicting"
    xbrl_basis = next(iter(fact_basis))
    if metadata_basis != xbrl_basis:
        return None, f"NSE/XBRL reporting-basis mismatch: index={metadata_basis}, XBRL={xbrl_basis}"
    if metadata_basis != TARGET_BASIS:
        return None, "NSE and XBRL both mark this filing as Standalone"

    # Many NSE files state the basis in a dedicated fact rather than as a
    # dimension on every context. This is accepted only after index and XBRL
    # basis values agree. Contexts explicitly marked as the opposite basis or
    # mixed are still excluded, as are dimensional detail contexts.
    target_contexts = {
        cid: ctx for cid, ctx in contexts.items()
        if ctx["basis"] not in ("Standalone", "MIXED")
        and not ctx["other_dimensions"]
    }
    if not target_contexts:
        return None, "No clean context for consolidated statement facts"

    quarter_end = record_quarter_end(record)
    if quarter_end is None:
        # A usable duration end date can establish the end date if the index field is absent.
        possible_ends = [
            ctx["end"] for ctx in target_contexts.values()
            if ctx["start"] and ctx["end"] and 75 <= (ctx["end"] - ctx["start"]).days <= 110
        ]
        if not possible_ends:
            return None, "Quarter-end date missing and no three-month XBRL context found"
        quarter_end = max(possible_ends)

    duration_candidates = {}
    instant_context_ids = set()
    for cid, ctx in target_contexts.items():
        if ctx["instant"] == quarter_end:
            instant_context_ids.add(cid)
        if ctx["start"] and ctx["end"] == quarter_end:
            days = (ctx["end"] - ctx["start"]).days
            if 75 <= days <= 110:
                duration_candidates.setdefault(days, set()).add(cid)
    if not duration_candidates:
        return None, "No explicit 75-110 day quarter context; refusing to substitute YTD/annual figures"
    quarter_context_ids = duration_candidates[min(duration_candidates)]

    row = {
        "Symbol": record_symbol(record),
        "Quarter_End": quarter_end.isoformat(),
        "Reporting_Basis": TARGET_BASIS,
        "Revenue_Operations_Cr": None,
        "Net_Profit_Cr": None,
        "Basic_EPS_Rs": None,
        "Total_Assets_Cr": None,
        "Total_Equity_Cr": None,
        "Total_Borrowings_Cr": None,
        "Reporting_Type": str(first_value(record, "type", "reportingType") or ""),
        "Filing_Audit_Status": str(first_value(record, "audited", "auditStatus") or ""),
        "Filing_Consolidation_Status": str(first_value(record, "consolidated", "consolidatedStandalone", "natureOfReport") or ""),
        "Submission_Subtype": str(first_value(record, "type_Sub", "typeSub") or ""),
        "Revision_Date": str(first_value(record, "revised_Date", "revisedDate") or ""),
        "Revision_Remark": str(first_value(record, "revision_Remark", "revisionRemark") or ""),
        "Sequence_ID": str(first_value(record, "seq_Id", "seqId") or ""),
        "Filing_Published_At": (xbrl_upload_date(url) or record_publish_date(record) or date.min).isoformat(),
        "Source_URL": url,
        "Quarter_Context_Days": min(duration_candidates),
        "Quarter_Context_IDs": "|".join(sorted(quarter_context_ids)),
        "Instant_Context_IDs": "|".join(sorted(instant_context_ids)),
        "Basis_Verification": "NSE filing index label matches XBRL NatureOfReportStandaloneConsolidated fact",
    }
    currency_fields = ["Revenue_Operations_Cr", "Net_Profit_Cr"]
    for field in currency_fields:
        value = get_fact(root, ALIASES[field], quarter_context_ids)
        row[field] = value / 10_000_000 if value is not None else None
    eps = get_fact(root, ALIASES["Basic_EPS_Rs"], quarter_context_ids)
    row["Basic_EPS_Rs"] = eps
    for field in ("Total_Assets_Cr", "Total_Equity_Cr"):
        value = get_fact(root, ALIASES[field], instant_context_ids)
        row[field] = value / 10_000_000 if value is not None else None

    total_borrowing = get_fact(root, ["Borrowings", "TotalBorrowings"], instant_context_ids)
    if total_borrowing is not None:
        row["Total_Borrowings_Cr"] = total_borrowing / 10_000_000
    else:
        current = get_fact(root, CURRENT_BORROWING_ALIASES, instant_context_ids)
        noncurrent = get_fact(root, NONCURRENT_BORROWING_ALIASES, instant_context_ids)
        # Both explicit components are required. An absent fact is not presumed to be zero.
        if current is not None and noncurrent is not None:
            row["Total_Borrowings_Cr"] = (current + noncurrent) / 10_000_000

    missing = [field for field in VALUE_FIELDS if row[field] is None]
    row["Missing_Fields"] = "|".join(missing)
    row["Fundamentals_Complete"] = not missing
    return row, ""


def save_frame(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, na_rep="")


def build_change_report(results: pd.DataFrame) -> pd.DataFrame:
    if results.empty:
        return results
    frame = results.copy()
    frame["Quarter_End_Date"] = pd.to_datetime(frame["Quarter_End"], errors="coerce")
    for field in VALUE_FIELDS:
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
    frame = frame.sort_values(["Symbol", "Quarter_End_Date", "Filing_Published_At"])
    for metric, field in METRICS.items():
        frame[f"{metric}_QoQ_Change_Pct"] = None
        frame[f"{metric}_YoY_Change_Pct"] = None
        for _symbol, indexes in frame.groupby("Symbol").groups.items():
            idxs = list(indexes)
            for pos, idx in enumerate(idxs):
                current_date = frame.at[idx, "Quarter_End_Date"]
                current_value = frame.at[idx, field]
                if pd.isna(current_date) or pd.isna(current_value):
                    continue
                for lag, output in ((1, f"{metric}_QoQ_Change_Pct"), (4, f"{metric}_YoY_Change_Pct")):
                    prior_pos = pos - lag
                    if prior_pos < 0:
                        continue
                    prior_idx = idxs[prior_pos]
                    prior_date = frame.at[prior_idx, "Quarter_End_Date"]
                    prior_value = frame.at[prior_idx, field]
                    if pd.isna(prior_date) or pd.isna(prior_value) or prior_value == 0:
                        continue
                    days = (current_date - prior_date).days
                    allowed = (70 <= days <= 115) if lag == 1 else (330 <= days <= 400)
                    if allowed:
                        frame.at[idx, output] = round((current_value / prior_value - 1) * 100, 4)
    return frame.drop(columns=["Quarter_End_Date"], errors="ignore")


def main() -> None:
    existing_results = read_csv(RESULTS_FILE)
    existing_unverified = read_csv(UNVERIFIED_FILE)
    existing_processed = read_csv(PROCESSED_FILE)
    if "Parser_Version" not in existing_processed.columns:
        existing_processed["Parser_Version"] = ""
    processed_urls = set(existing_processed.loc[existing_processed["Parser_Version"].astype(str).eq(PARSER_VERSION), "Source_URL"].astype(str))
    session = make_session()
    index_records = fetch_recent_records(session)

    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    by_url: dict[str, dict] = {}
    for rec in index_records:
        url = source_url(rec)
        sym = record_symbol(rec)
        qend = record_quarter_end(rec)
        pubdate = record_publish_date(rec)
        if not url or not sym:
            continue
        if (pubdate and pubdate < cutoff) and (not qend or qend < cutoff):
            continue
        # Keep the newest API metadata for repeated references to the same XBRL URL.
        old = by_url.get(url)
        if old is None or (record_publish_date(rec) or date.min) >= (record_publish_date(old) or date.min):
            by_url[url] = rec

    candidates = sorted(
        [(url, rec) for url, rec in by_url.items() if url not in processed_urls],
        key=lambda pair: record_publish_date(pair[1]) or date.min,
        reverse=True,
    )
    print(f"Recent unique XBRL URLs: {len(by_url)}; unprocessed: {len(candidates)}")
    new_rows: list[dict] = []
    new_unverified: list[dict] = []
    new_processed: list[dict] = []
    attempted = 0
    consecutive_download_errors = 0
    for url, rec in candidates:
        if attempted >= MAX_NEW_XBRL_PER_RUN:
            break
        attempted += 1
        try:
            resp = session.get(url, headers={"Referer": "https://www.nseindia.com/"}, timeout=40)
            if resp.status_code in (403, 429, 500, 502, 503, 504):
                raise requests.HTTPError(f"XBRL HTTP {resp.status_code}")
            resp.raise_for_status()
            row, reason = parse_xbrl(resp.content, rec, url)
            consecutive_download_errors = 0
        except Exception as exc:
            consecutive_download_errors += 1
            print(f"Download error for {record_symbol(rec)}: {exc}")
            if consecutive_download_errors >= 12:
                raise RuntimeError("12 consecutive XBRL download errors; stopping to avoid repeated blocked requests.")
            time.sleep(0.8)
            continue

        processed_row = {
            "Source_URL": url,
            "Symbol": record_symbol(rec),
            "Quarter_End": (record_quarter_end(rec) or "").isoformat() if record_quarter_end(rec) else "",
            "Status": "VERIFIED" if row else "UNVERIFIED",
            "Reason": reason,
            "Processed_At_UTC": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "Parser_Version": PARSER_VERSION,
        }
        new_processed.append(processed_row)
        if row:
            new_rows.append(row)
        else:
            new_unverified.append({
                "Symbol": record_symbol(rec),
                "Quarter_End": record_quarter_end(rec).isoformat() if record_quarter_end(rec) else "",
                "Source_URL": url,
                "Reason": reason,
                "Submission_Subtype": str(first_value(rec, "type_Sub", "typeSub") or ""),
                "Revision_Date": str(first_value(rec, "revised_Date", "revisedDate") or ""),
            })
        if attempted % 50 == 0:
            print(f"Examined {attempted}/{min(len(candidates), MAX_NEW_XBRL_PER_RUN)} new files...")
        time.sleep(0.08)

    result_columns = [
        "Symbol", "Quarter_End", "Reporting_Basis", *VALUE_FIELDS, "Missing_Fields",
        "Fundamentals_Complete", "Reporting_Type", "Submission_Subtype", "Revision_Date",
        "Revision_Remark", "Sequence_ID", "Filing_Audit_Status", "Filing_Consolidation_Status", "Filing_Published_At", "Source_URL",
        "Quarter_Context_Days", "Quarter_Context_IDs", "Instant_Context_IDs", "Basis_Verification",
    ]
    all_results = pd.concat([existing_results, pd.DataFrame(new_rows)], ignore_index=True, sort=False)
    if not all_results.empty:
        for col in result_columns:
            if col not in all_results.columns:
                all_results[col] = ""
        all_results["Quarter_End"] = all_results["Quarter_End"].astype(str)
        all_results["Symbol"] = all_results["Symbol"].astype(str).str.upper()
        all_results["Filing_Published_At"] = all_results["Filing_Published_At"].astype(str)
        all_results = all_results[all_results["Symbol"].ne("") & all_results["Quarter_End"].ne("")]
        # One latest verified filing per symbol and quarter; source/revision metadata stays attached.
        all_results = all_results.sort_values(["Filing_Published_At", "Revision_Date"], kind="stable")
        all_results = all_results.drop_duplicates(["Symbol", "Quarter_End"], keep="last")
        all_results = all_results[result_columns].sort_values(["Symbol", "Quarter_End"])
        all_results["Fundamentals_Complete"] = all_results["Fundamentals_Complete"].astype(str)
        save_frame(all_results, RESULTS_FILE)
        full_change_report = build_change_report(all_results)
        # The final comparison report only surfaces companies that pass the
        # complete annual fundamental screen. Raw verified quarter facts remain
        # in quarterly_results.csv for audit/review.
        if ANNUAL_QUALITY_FILE.exists():
            annual_quality = read_csv(ANNUAL_QUALITY_FILE)
            if not annual_quality.empty and {"Symbol", "Full_Fundamental_Criteria_Pass"}.issubset(annual_quality.columns):
                eligible = set(annual_quality.loc[
                    annual_quality["Full_Fundamental_Criteria_Pass"].astype(str).str.lower().isin(["true", "1", "yes"]),
                    "Symbol"
                ].astype(str).str.upper())
                full_change_report = full_change_report[
                    full_change_report["Symbol"].astype(str).str.upper().isin(eligible)
                ]
            else:
                full_change_report = full_change_report.iloc[0:0]
        else:
            full_change_report = full_change_report.iloc[0:0]
        save_frame(full_change_report, COMPARE_FILE)
    else:
        save_frame(pd.DataFrame(columns=result_columns), RESULTS_FILE)
        save_frame(pd.DataFrame(columns=result_columns), COMPARE_FILE)

    unverified_columns = ["Symbol", "Quarter_End", "Source_URL", "Reason", "Submission_Subtype", "Revision_Date"]
    all_unverified = pd.concat([existing_unverified, pd.DataFrame(new_unverified)], ignore_index=True, sort=False)
    if not all_unverified.empty:
        for col in unverified_columns:
            if col not in all_unverified.columns:
                all_unverified[col] = ""
        all_unverified = all_unverified.drop_duplicates(["Source_URL"], keep="last")
        all_unverified = all_unverified[unverified_columns].sort_values(["Quarter_End", "Symbol"])
    else:
        all_unverified = pd.DataFrame(columns=unverified_columns)
    save_frame(all_unverified, UNVERIFIED_FILE)

    processed_columns = ["Source_URL", "Symbol", "Quarter_End", "Status", "Reason", "Processed_At_UTC", "Parser_Version"]
    all_processed = pd.concat([existing_processed, pd.DataFrame(new_processed)], ignore_index=True, sort=False)
    if not all_processed.empty:
        for col in processed_columns:
            if col not in all_processed.columns:
                all_processed[col] = ""
        all_processed = all_processed.drop_duplicates(["Source_URL"], keep="last")
        all_processed = all_processed[processed_columns]
    else:
        all_processed = pd.DataFrame(columns=processed_columns)
    save_frame(all_processed, PROCESSED_FILE)

    complete_count = 0
    result_count = len(all_results)
    if not all_results.empty and "Fundamentals_Complete" in all_results:
        complete_count = all_results["Fundamentals_Complete"].astype(str).str.lower().isin(["true", "1"]).sum()
    print(f"Done. Verified symbol-quarter rows={result_count}; six-field complete={complete_count}; newly examined={attempted}.")
    print(f"Saved: {RESULTS_FILE.name}, {COMPARE_FILE.name}, {UNVERIFIED_FILE.name}, {PROCESSED_FILE.name}")


if __name__ == "__main__":
    main()
