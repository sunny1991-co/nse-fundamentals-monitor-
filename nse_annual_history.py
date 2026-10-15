#!/usr/bin/env python3
"""Fetch audited consolidated annual XBRL facts for a rolling five-year history."""
from __future__ import annotations

import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
LANDING_URL = "https://www.nseindia.com/companies-listing/corporate-integrated-filing"
API_URL = "https://www.nseindia.com/api/integrated-filing-results"
PAGE_SIZE = 500
MAX_PAGES = 100
HISTORY_DAYS = 1500
MAX_XBRL_PER_RUN = 9000
MAX_DOWNLOAD_WORKERS = 8
PARSER_VERSION = "annual-v4"

RESULTS_FILE = ROOT / "annual_historical_results.csv"
UNVERIFIED_FILE = ROOT / "annual_historical_unverified.csv"
PROCESSED_FILE = ROOT / "annual_history_processed.csv"

ANNUAL_ALIASES = {
    "Revenue_Operations_Cr": ["RevenueFromOperations", "RevenueFromOperationsNetOfExciseDuty"],
    "Net_Profit_Cr": ["ProfitLossForPeriod", "ProfitLoss"],
    "Basic_EPS_Rs": [
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "BasicEarningsLossPerShare",
        "BasicEarningsLossPerShareFromContinuingOperations",
        "EarningsPerShareBasic",
        "BasicEPS",
    ],
    "Profit_Before_Tax_Cr": ["ProfitLossBeforeTax", "ProfitBeforeTax"],
    "Total_Expenses_Cr": ["TotalExpenses"],
    "Finance_Cost_Cr": ["FinanceCosts", "FinanceCost"],
    "Depreciation_Amortisation_Cr": ["DepreciationDepletionAndAmortisationExpense", "DepreciationAndAmortisationExpense"],
    "Cash_Flow_Operating_Cr": [
        "NetCashFlowsFromUsedInOperatingActivities",
        "NetCashFlowsFromOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
        "CashFlowsFromOperatingActivities",
        "NetCashFlowFromOperatingActivities",
    ],
}
INSTANT_ALIASES = {
    "Total_Assets_Cr": ["Assets", "TotalAssets"],
    "Total_Equity_Cr": ["Equity", "TotalEquity"],
    "Current_Liabilities_Cr": ["LiabilitiesCurrent", "CurrentLiabilities"],
    "Cash_CashEquivalents_Cr": ["CashAndCashEquivalents", "CashCashEquivalents", "CashAndCashEquivalentsAtCarryingValue"],
}
CAPEX_PPE_ALIASES = [
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    "PurchaseOfPropertyPlantAndEquipment",
    "PaymentsToAcquirePropertyPlantAndEquipment",
]
CAPEX_INTANGIBLE_ALIASES = [
    "PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
    "PurchaseOfIntangibleAssets",
]
CAPEX_COMBINED_ALIASES = [
    "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
    "PurchaseOfPropertyPlantAndEquipmentAndIntangibleAssets",
    "PaymentsToAcquireTangibleAssets",
]
CAPEX_ADDITIONAL_COMPONENTS = [
    ["PurchaseOfInvestmentPropertyClassifiedAsInvestingActivities", "PurchaseOfInvestmentProperty"],
    ["PurchaseOfGoodwillClassifiedAsInvestingActivities", "PurchaseOfGoodwill"],
    ["PurchaseOfBiologicalAssetsOtherThanBearerPlantsClassifiedAsInvestingActivities"],
]
CURRENT_BORROWING_ALIASES = ["BorrowingsCurrent", "CurrentBorrowings", "BorrowingsCurrentPortion"]
NONCURRENT_BORROWING_ALIASES = ["BorrowingsNoncurrent", "NoncurrentBorrowings", "BorrowingsNonCurrent"]
TOTAL_BORROWING_ALIASES = ["Borrowings", "TotalBorrowings"]
CORE = ["Revenue_Operations_Cr", "Net_Profit_Cr", "Basic_EPS_Rs", "Total_Assets_Cr", "Total_Equity_Cr", "Total_Borrowings_Cr"]


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def parse_date(value: Any) -> date | None:
    if value is None or str(value).strip() in ("", "nan", "NaT", "None"):
        return None
    value = str(value).strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%m-%Y %H:%M:%S", "%d-%b-%Y %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    dt = pd.to_datetime(value, errors="coerce", dayfirst=True)
    return None if pd.isna(dt) else dt.date()


def file_stamp_date(url: str) -> date | None:
    match = re.search(r"_(\d{14})_WEB\.xml(?:$|\?)", url, re.I)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d%m%Y%H%M%S").date()
    except ValueError:
        return None


def value(record: dict, *keys: str) -> Any:
    lower = {str(k).lower(): v for k, v in record.items()}
    for key in keys:
        v = lower.get(key.lower())
        if v not in (None, ""):
            return v
    return None


def symbol(record: dict) -> str:
    return str(value(record, "symbol", "sm_symbol") or "").strip().upper()


def xbrl_url(record: dict) -> str:
    return str(value(record, "xbrl", "xbrlUrl") or "").strip()


def end_date(record: dict) -> date | None:
    return parse_date(value(record, "qe_Date", "quarterEndDate", "periodEndDate"))


def published_date(record: dict) -> date | None:
    return parse_date(value(record, "revised_Date", "broadcast_Date", "creation_Date")) or file_stamp_date(xbrl_url(record)) or end_date(record)


def normalize_basis(v: Any) -> str:
    text = str(v or "").strip().lower()
    if text == "consolidated":
        return "Consolidated"
    if text in {"standalone", "separate"}:
        return "Standalone"
    return "UNVERIFIED"


def collect_items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "records", "result", "items"):
            v = payload.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                found = collect_items(v)
                if found:
                    return found
    return []


def session_create() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": LANDING_URL,
        "X-Requested-With": "XMLHttpRequest",
    })
    response = s.get(LANDING_URL, timeout=30)
    response.raise_for_status()
    return s


def api_page(s: requests.Session, page: int) -> list[dict]:
    params = {"type": "Integrated Filing- Financials", "index": "equities", "page": page, "size": PAGE_SIZE}
    last = None
    for attempt in range(4):
        try:
            r = s.get(API_URL, params=params, timeout=35)
            r.raise_for_status()
            return collect_items(r.json())
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"NSE API page {page} failed: {last}")


def all_recent_records(s: requests.Session) -> list[dict]:
    cutoff = date.today() - timedelta(days=HISTORY_DAYS)
    out: list[dict] = []
    for page in range(MAX_PAGES):
        batch = api_page(s, page)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        # Do not stop early from a publication-date heuristic: older annual
        # filings/revisions can appear out of chronological order in the NSE index.
        time.sleep(0.03)
    print(f"Annual index records examined: {len(out)}; history cutoff: {cutoff}")
    return out


def parse_contexts(root: ET.Element) -> dict[str, dict]:
    contexts = {}
    for node in root.iter():
        if local_name(node.tag).lower() != "context" or not node.attrib.get("id"):
            continue
        period = next((x for x in node.iter() if local_name(x.tag).lower() == "period"), None)
        if period is None:
            continue
        instant = start = end = None
        for child in period:
            name = local_name(child.tag).lower()
            if name == "instant":
                instant = parse_date(child.text)
            elif name == "startdate":
                start = parse_date(child.text)
            elif name == "enddate":
                end = parse_date(child.text)
        members = []
        for child in node.iter():
            if local_name(child.tag).lower() == "explicitmember":
                members.append((str(child.attrib.get("dimension", "")).split(":")[-1].lower(), str(child.text or "").split(":")[-1].lower()))
        other_dimensions = []
        basis = "UNVERIFIED"
        for axis, member in members:
            combined = axis + " " + member
            if "consolidated" in combined and "separate" not in combined and "standalone" not in combined:
                basis = "Consolidated"
            elif "standalone" in combined or "separate" in member:
                basis = "Standalone"
            is_basis_dimension = (
                "consolidated" in member or "standalone" in member or "separate" in member
                or "consolidated" in axis or "natureofreport" in axis
                or "typeofstatement" in axis or "financialstatement" in axis
            )
            if not is_basis_dimension:
                other_dimensions.append((axis, member))
        contexts[node.attrib["id"]] = {
            "instant": instant, "start": start, "end": end,
            "basis": basis, "other_dimensions": other_dimensions,
        }
    return contexts


def fact_basis(root: ET.Element) -> set[str]:
    tags = {"natureofreportstandaloneconsolidated", "natureofreport", "natureofstatement", "natureoffinancialstatements"}
    found = set()
    for node in root.iter():
        if local_name(node.tag).lower() not in tags:
            continue
        text = (node.text or "").strip().lower()
        if text == "consolidated":
            found.add("Consolidated")
        elif text in {"standalone", "separate"}:
            found.add("Standalone")
    return found


def get_fact(root: ET.Element, aliases: list[str], context_ids: set[str]) -> float | None:
    if not context_ids:
        return None
    rank = {x.lower(): i for i, x in enumerate(aliases)}
    candidates = []
    for node in root.iter():
        name = local_name(node.tag).lower()
        if name not in rank or node.attrib.get("contextRef") not in context_ids:
            continue
        text = (node.text or "").strip().replace(",", "")
        if not text or node.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
            continue
        try:
            v = float(text)
        except ValueError:
            continue
        if v == v:
            candidates.append((rank[name], v))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def cr(v: float | None) -> float | None:
    return None if v is None else v / 10_000_000


def parse_annual(record: dict, url: str, body: bytes) -> tuple[dict | None, str]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        return None, f"Invalid XML: {exc}"
    metadata_basis = normalize_basis(value(record, "consolidated", "consolidatedStandalone", "natureOfReport"))
    xml_basis = fact_basis(root)
    audit = str(value(record, "audited", "auditStatus") or "")
    if metadata_basis != "Consolidated":
        return None, f"NSE basis is {metadata_basis}, not Consolidated"
    if xml_basis != {"Consolidated"}:
        return None, f"XBRL reporting-basis fact missing/conflicting: {sorted(xml_basis)}"
    if "audit" not in audit.lower() or "un-audit" in audit.lower() or "unaudit" in audit.lower():
        return None, f"NSE filing is not explicitly audited: {audit or 'missing audit label'}"

    fiscal_end = end_date(record)
    if not fiscal_end or fiscal_end.month != 3 or fiscal_end.day != 31:
        return None, "Not a March 31 financial year-end filing"
    contexts = parse_contexts(root)
    annual_by_days: dict[int, set[str]] = {}
    instant_ids = set()
    for cid, ctx in contexts.items():
        if ctx["basis"] in {"Standalone", "MIXED"} or ctx["other_dimensions"]:
            continue
        if ctx["instant"] == fiscal_end:
            instant_ids.add(cid)
        if ctx["start"] and ctx["end"] == fiscal_end:
            days = (ctx["end"] - ctx["start"]).days
            if 330 <= days <= 400:
                annual_by_days.setdefault(days, set()).add(cid)
    if not annual_by_days:
        return None, "No unambiguous 330-400 day annual XBRL context"
    annual_days = min(annual_by_days)
    duration_ids = annual_by_days[annual_days]
    facts = {}
    for name, aliases in ANNUAL_ALIASES.items():
        raw_fact = get_fact(root, aliases, duration_ids)
        facts[name] = raw_fact if name == "Basic_EPS_Rs" else cr(raw_fact)
    for name, aliases in INSTANT_ALIASES.items():
        facts[name] = cr(get_fact(root, aliases, instant_ids))
    total_debt = get_fact(root, TOTAL_BORROWING_ALIASES, instant_ids)
    current_debt = get_fact(root, CURRENT_BORROWING_ALIASES, instant_ids)
    noncurrent_debt = get_fact(root, NONCURRENT_BORROWING_ALIASES, instant_ids)
    if total_debt is not None:
        facts["Total_Borrowings_Cr"] = cr(total_debt)
    elif current_debt is not None and noncurrent_debt is not None:
        facts["Total_Borrowings_Cr"] = cr(current_debt + noncurrent_debt)
    else:
        facts["Total_Borrowings_Cr"] = None
    ppe_capex = get_fact(root, CAPEX_PPE_ALIASES, duration_ids)
    intangible_capex = get_fact(root, CAPEX_INTANGIBLE_ALIASES, duration_ids)
    extra_capex_values = [
        get_fact(root, aliases, duration_ids)
        for aliases in CAPEX_ADDITIONAL_COMPONENTS
    ]
    disclosed_extra_capex = sum(abs(v) for v in extra_capex_values if v is not None)
    capex_complete = False
    capex_raw = None
    capex_method = ""
    if ppe_capex is not None and intangible_capex is not None:
        capex_raw = abs(ppe_capex) + abs(intangible_capex) + disclosed_extra_capex
        capex_complete = True
        capex_method = "PPE+Intangible purchases plus separately disclosed additional asset purchases"
    else:
        # A combined total is used only when both main components are not available;
        # never add individual components on top of a combined total.
        combined_capex = get_fact(root, CAPEX_COMBINED_ALIASES, duration_ids)
        if combined_capex is not None:
            capex_raw = abs(combined_capex) + disclosed_extra_capex
            capex_complete = True
            capex_method = "Reported combined PPE/intangible purchases plus separately disclosed additional asset purchases"
    facts["CapEx_Cr"] = cr(capex_raw) if capex_raw is not None else None
    facts["CapEx_Components_Verified"] = capex_complete
    facts["CapEx_Method"] = capex_method
    core_missing = [k for k in CORE if facts.get(k) is None]
    upload = file_stamp_date(url) or published_date(record) or date.min
    row = {
        "Symbol": symbol(record),
        "Company": str(value(record, "smName", "cmName", "companyName") or ""),
        "Fiscal_Year_End": fiscal_end.isoformat(),
        "Financial_Year": f"{fiscal_end.year-1}-{str(fiscal_end.year)[-2:]}",
        "Reporting_Basis": "Consolidated",
        "Audit_Status": audit,
        "NSE_Consolidation_Status": str(value(record, "consolidated") or ""),
        "Revenue_Operations_Cr": facts.get("Revenue_Operations_Cr"),
        "Net_Profit_Cr": facts.get("Net_Profit_Cr"),
        "Basic_EPS_Rs": facts.get("Basic_EPS_Rs"),
        "Total_Assets_Cr": facts.get("Total_Assets_Cr"),
        "Total_Equity_Cr": facts.get("Total_Equity_Cr"),
        "Total_Borrowings_Cr": facts.get("Total_Borrowings_Cr"),
        "Profit_Before_Tax_Cr": facts.get("Profit_Before_Tax_Cr"),
        "Total_Expenses_Cr": facts.get("Total_Expenses_Cr"),
        "Finance_Cost_Cr": facts.get("Finance_Cost_Cr"),
        "Depreciation_Amortisation_Cr": facts.get("Depreciation_Amortisation_Cr"),
        "Cash_Flow_Operating_Cr": facts.get("Cash_Flow_Operating_Cr"),
        "CapEx_Cr": facts.get("CapEx_Cr"),
        "CapEx_Components_Verified": facts.get("CapEx_Components_Verified", False),
        "CapEx_Method": facts.get("CapEx_Method", ""),
        "Current_Liabilities_Cr": facts.get("Current_Liabilities_Cr"),
        "Cash_CashEquivalents_Cr": facts.get("Cash_CashEquivalents_Cr"),
        "Annual_Context_Days": annual_days,
        "Annual_Context_IDs": "|".join(sorted(duration_ids)),
        "Instant_Context_IDs": "|".join(sorted(instant_ids)),
        "Source_URL": url,
        "Filing_Published_At": upload.isoformat(),
        "Revision_Date": str(value(record, "revised_Date") or ""),
        "Submission_Subtype": str(value(record, "type_Sub") or ""),
        "Revision_Remark": str(value(record, "revision_Remark") or ""),
        "Sequence_ID": str(value(record, "seq_Id") or ""),
        "NSE_Consolidation_Verified": True,
        "Six_Core_Fields_Complete": not core_missing,
        "Missing_Core_Fields": "|".join(core_missing),
        "Status": "VERIFIED" if not core_missing else "VERIFIED_WITH_MISSING_CORE_FIELDS",
    }
    return row, ""



_worker_state = threading.local()


def process_annual_task(url: str, rec: dict) -> tuple[dict | None, dict | None, dict]:
    """Download and parse one official annual filing using a worker-local session."""
    row = None
    reason = ""
    status = "UNVERIFIED"
    for attempt in range(3):
        try:
            sess = getattr(_worker_state, "session", None)
            if sess is None:
                sess = session_create()
                _worker_state.session = sess
            response = sess.get(
                url,
                headers={"Referer": "https://www.nseindia.com/"},
                timeout=30,
            )
            response.raise_for_status()
            row, reason = parse_annual(rec, url, response.content)
            if row:
                status = row["Status"]
            break
        except Exception as exc:
            reason = f"Download/parser error: {exc}"
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))

    processed = {
        "Source_URL": url,
        "Symbol": symbol(rec),
        "Fiscal_Year_End": end_date(rec).isoformat() if end_date(rec) else "",
        "Status": status,
        "Reason": reason,
        "Processed_At_UTC": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "Parser_Version": PARSER_VERSION,
    }
    unverified = None
    if row is None:
        unverified = {
            "Symbol": symbol(rec),
            "Fiscal_Year_End": end_date(rec).isoformat() if end_date(rec) else "",
            "Source_URL": url,
            "Reason": reason,
            "Audit_Status": str(value(rec, "audited") or ""),
            "NSE_Consolidation_Status": str(value(rec, "consolidated") or ""),
            "Revision_Date": str(value(rec, "revised_Date") or ""),
        }
    return row, unverified, processed


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def save_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, na_rep="")


def main():
    cutoff = date.today() - timedelta(days=HISTORY_DAYS)
    old_results = read_csv(RESULTS_FILE)
    old_unverified = read_csv(UNVERIFIED_FILE)
    old_processed = read_csv(PROCESSED_FILE)
    if "Parser_Version" not in old_processed.columns:
        old_processed["Parser_Version"] = ""
    if "Source_URL" not in old_processed.columns:
        old_processed["Source_URL"] = ""
    if "Status" not in old_processed.columns:
        old_processed["Status"] = ""
    # Cache only successful verifications. A failed download, 403/429, or
    # inconclusive XBRL parse must be retried on later scheduled runs; otherwise
    # a transient NSE failure permanently blocks annual history coverage.
    # Never promote an unverified filing into the verified results.
    successful_statuses = {"VERIFIED", "VERIFIED_WITH_MISSING_CORE_FIELDS"}
    processed_urls = set(
        old_processed.loc[
            old_processed["Parser_Version"].astype(str).eq(PARSER_VERSION)
            & old_processed["Status"].astype(str).isin(successful_statuses),
            "Source_URL"
        ].astype(str)
    )

    # Restrict history downloads to the symbols in the requested annual baseline.
    # We still verify their original NSE filing metadata and exact XBRL source.
    target_symbols: set[str] = set()
    for filename in ("FY2425.csv", "NSE_FY2025-26_Consolidated_Fundamentals.csv"):
        path = ROOT / filename
        if not path.exists():
            raise FileNotFoundError(f"Required baseline file not found: {filename}")
        baseline = read_csv(path)
        if "Symbol" not in baseline.columns:
            raise ValueError(f"{filename} has no Symbol column")
        target_symbols.update(
            str(v).strip().upper() for v in baseline["Symbol"].astype(str)
            if str(v).strip() and str(v).strip().lower() not in {"nan", "none", "<na>"}
        )
    if not target_symbols:
        raise RuntimeError("No baseline symbols found; refusing to create an empty annual screen.")
    if not old_results.empty and "Symbol" in old_results.columns:
        target_symbols.update(
            str(v).strip().upper() for v in old_results["Symbol"].astype(str)
            if str(v).strip() and str(v).strip().lower() not in {"nan", "none", "<na>"}
        )
    print(f"Target baseline symbols: {len(target_symbols)}", flush=True)

    s = session_create()
    indexed = all_recent_records(s)
    # Pick the newest audited-consolidated filing per symbol and fiscal year.
    # This avoids spending time downloading stale originals and older revisions.
    latest_by_company_year: dict[tuple[str, str], dict] = {}
    for rec in indexed:
        sym = symbol(rec)
        d = end_date(rec)
        url = xbrl_url(rec)
        if sym not in target_symbols or not d or d < cutoff or d.month != 3 or d.day != 31 or not url:
            continue
        if normalize_basis(value(rec, "consolidated")) != "Consolidated":
            continue
        if not str(value(rec, "audited") or "").lower().startswith("audited"):
            continue
        key = (sym, d.isoformat())
        prior = latest_by_company_year.get(key)
        if prior is None or (published_date(rec) or date.min) >= (published_date(prior) or date.min):
            latest_by_company_year[key] = rec

    by_url = {xbrl_url(rec): rec for rec in latest_by_company_year.values() if xbrl_url(rec)}
    pending = sorted(
        [(url, rec) for url, rec in by_url.items() if url not in processed_urls],
        key=lambda item: published_date(item[1]) or date.min,
        reverse=True,
    )[:MAX_XBRL_PER_RUN]
    print(
        f"Annual index records examined: {len(indexed)}; latest audited consolidated symbol-years: {len(by_url)}; "
        f"to process this run: {len(pending)}; workers: {MAX_DOWNLOAD_WORKERS}",
        flush=True,
    )

    new_rows, new_unverified, new_processed = [], [], []
    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
        futures = {pool.submit(process_annual_task, url, rec): (url, rec) for url, rec in pending}
        for idx, future in enumerate(as_completed(futures), 1):
            try:
                row, unverified_row, processed_row = future.result()
            except Exception as exc:
                url, rec = futures[future]
                reason = f"Worker exception: {exc}"
                row = None
                unverified_row = {
                    "Symbol": symbol(rec),
                    "Fiscal_Year_End": end_date(rec).isoformat() if end_date(rec) else "",
                    "Source_URL": url,
                    "Reason": reason,
                    "Audit_Status": str(value(rec, "audited") or ""),
                    "NSE_Consolidation_Status": str(value(rec, "consolidated") or ""),
                    "Revision_Date": str(value(rec, "revised_Date") or ""),
                }
                processed_row = {
                    "Source_URL": url, "Symbol": symbol(rec),
                    "Fiscal_Year_End": end_date(rec).isoformat() if end_date(rec) else "",
                    "Status": "UNVERIFIED", "Reason": reason,
                    "Processed_At_UTC": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "Parser_Version": PARSER_VERSION,
                }
            if row is not None:
                new_rows.append(row)
            if unverified_row is not None:
                new_unverified.append(unverified_row)
            new_processed.append(processed_row)
            if idx % 100 == 0 or idx == len(pending):
                print(f"Processed {idx}/{len(pending)} annual XBRL files", flush=True)

    results = pd.concat([old_results, pd.DataFrame(new_rows)], ignore_index=True, sort=False)
    if not results.empty:
        results["Fiscal_Year_End"] = results["Fiscal_Year_End"].astype(str)
        results["Symbol"] = results["Symbol"].astype(str).str.upper()
        results["Filing_Published_At"] = results["Filing_Published_At"].astype(str)
        results = results[results["Symbol"].ne("") & results["Fiscal_Year_End"].ne("")]
        results = results.sort_values(["Filing_Published_At", "Revision_Date"], kind="stable").drop_duplicates(["Symbol", "Fiscal_Year_End"], keep="last")
        results = results.sort_values(["Symbol", "Fiscal_Year_End"])
    save_csv(results, RESULTS_FILE)

    # Remove prior unverified notes for URLs that have now parsed successfully.
    successful_urls = {str(row.get("Source_URL", "")) for row in new_rows}
    if not old_unverified.empty and "Source_URL" in old_unverified.columns and successful_urls:
        old_unverified = old_unverified[~old_unverified["Source_URL"].astype(str).isin(successful_urls)]
    unverified = pd.concat([old_unverified, pd.DataFrame(new_unverified)], ignore_index=True, sort=False)
    if not unverified.empty:
        unverified = unverified.drop_duplicates(["Source_URL"], keep="last").sort_values(["Fiscal_Year_End", "Symbol"])
    save_csv(unverified, UNVERIFIED_FILE)

    processed = pd.concat([old_processed, pd.DataFrame(new_processed)], ignore_index=True, sort=False)
    if not processed.empty:
        processed = processed.drop_duplicates(["Source_URL"], keep="last")
    save_csv(processed, PROCESSED_FILE)
    print(f"Annual history rows saved: {len(results)}; annual XBRL URL ledger: {len(processed)}", flush=True)
    if not results.empty:
        years = sorted(results["Financial_Year"].astype(str).unique())
        print("Years available:", ", ".join(years), flush=True)
    print("Full annual dataset remains incomplete until all candidate filings and required fields are resolved.", flush=True)


if __name__ == "__main__":
    main()
