#!/usr/bin/env python3
"""Offline audit of official NSE pre-integrated-filing annual CSV exports.

Never treats a filing index or a fourth-quarter result as audited annual XBRL
facts. It only identifies possible primary-source documents for independent
verification; it does not modify the verified annual history or trading gates.
"""
from __future__ import annotations

import csv
import hashlib
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
LEGACY_YEARS = {"2022-23": "2023-03-31", "2023-24": "2024-03-31"}
BASELINE_FILES = ("FY2425.csv", "NSE_FY2025-26_Consolidated_Fundamentals.csv")
HISTORY_FILE = ROOT / "annual_historical_results.csv"
MANIFEST_FILE = ROOT / "legacy_annual_recovery_manifest.csv"
SUMMARY_FILE = ROOT / "legacy_annual_recovery_summary.csv"
ARCHIVE = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"

MANIFEST_FIELDS = (
    "Symbol", "Company", "Financial_Year", "Fiscal_Year_End", "Recovery_Status",
    "Source_URL", "Official_Archive", "Index_File", "Index_SHA256",
    "Review_Reason",
)


def csv_rows(path: Path) -> list[dict]:
    if not path.exists() or not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def normalize_header(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).strip().lower())


def field(row: dict, *aliases: str) -> str:
    folded = {normalize_header(key): str(value or "").strip() for key, value in row.items()}
    for key in aliases:
        value = folded.get(normalize_header(key))
        if value:
            return value
    return ""


def normalize_company(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().casefold())


def symbol_universe(root: Path) -> tuple[dict[str, str], dict[str, set[str]]]:
    companies = {}
    names = defaultdict(set)
    for filename in BASELINE_FILES:
        for row in csv_rows(root / filename):
            symbol = field(row, "Symbol").upper()
            company = field(row, "Company", "Name of Company")
            if not symbol:
                continue
            if symbol not in companies or (not companies[symbol] and company):
                companies[symbol] = company
            if company:
                names[normalize_company(company)].add(symbol)
    return companies, names


def parse_end(raw: str) -> str:
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except (ValueError, AttributeError):
            pass
    return ""


def official_xbrl_link(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.hostname == "nsearchives.nseindia.com"
            and parts.port is None
            and parts.path.startswith("/corporate/xbrl/")
            and parts.path.lower().endswith(".xml")
            and not parts.username
            and not parts.password
        )
    except ValueError:
        return False


def audited_annual_consolidated(row: dict, expected_end: str) -> tuple[bool, str]:
    end = parse_end(field(row, "Period Ended", "Quarter End Date", "Fiscal Year End", "To Date"))
    if end != expected_end:
        return False, "FISCAL_YEAR_END_NOT_MATCHED"
    audit = field(row, "Audited / Unaudited", "Audited", "Audit Status").lower()
    if not audit or "unaudit" in audit.replace("-", "").replace(" ", "") or audit != "audited":
        return False, "AUDITED_STATUS_NOT_EXPLICIT"
    basis = field(row, "Consolidated / Non-Consolidated", "Consolidated / Standalone", "Consolidated", "Reporting Basis").lower().strip()
    if basis != "consolidated":
        return False, "CONSOLIDATED_STATUS_NOT_EXPLICIT"
    period = field(row, "Period", "Reporting Period", "Relating To").lower()
    if "quarter" in period or "q4" in period or "fourth" in period:
        return False, "QUARTERLY_IS_NOT_ANNUAL"
    if not any(key in period for key in ("annual", "yearly", "full year", "twelve month", "12 month", "year ended")):
        return False, "ANNUAL_PERIOD_NOT_EXPLICIT"
    url = field(row, "XBRL URL", "XBRL File Link", "XBRL", "Source URL")
    if not official_xbrl_link(url):
        return False, "OFFICIAL_XBRL_XML_URL_MISSING"
    return True, ""


def verified_legacy_years(root: Path) -> set[tuple[str, str]]:
    verified = set()
    for row in csv_rows(root / HISTORY_FILE.name):
        year = field(row, "Financial_Year")
        symbol = field(row, "Symbol").upper()
        if year not in LEGACY_YEARS or not symbol:
            continue
        flag = field(row, "NSE_Consolidation_Verified").lower() in {"true", "1", "yes"}
        if (
            flag and field(row, "Reporting_Basis").lower() == "consolidated"
            and field(row, "Audit_Status").lower() == "audited"
            and official_xbrl_link(field(row, "Source_URL"))
        ):
            verified.add((symbol, year))
    return verified


def build(root: Path = ROOT) -> tuple[list[dict], list[dict]]:
    companies, names = symbol_universe(root)
    if not companies:
        raise ValueError("Both NSE baseline files are missing or contain no symbols.")
    verified = verified_legacy_years(root)
    candidates = {}
    sources = {}
    for year, expected_end in LEGACY_YEARS.items():
        filename = f"nse_legacy_financial_results_{year}.csv"
        path = root / filename
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        sources[year] = (filename, digest)
        for row in csv_rows(path):
            symbol = field(row, "Symbol", "Sm Symbol").upper()
            if not symbol:
                matching = names.get(normalize_company(field(row, "Company Name", "Name of Company")))
                symbol = next(iter(matching)) if matching and len(matching) == 1 else ""
            if symbol not in companies:
                continue
            ok, reason = audited_annual_consolidated(row, expected_end)
            if not ok:
                continue
            url = field(row, "XBRL URL", "XBRL File Link", "XBRL", "Source URL")
            key = (symbol, year)
            # Multiple revisions must be independently checked; do not
            # silently bless a preferred record using CSV order.
            candidates.setdefault(key, set()).add(url)

    manifest = []
    summary = []
    for year, expected_end in LEGACY_YEARS.items():
        year_verified = year_candidates = 0
        for symbol, company in sorted(companies.items()):
            key = (symbol, year)
            paths = candidates.get(key, set())
            if key in verified:
                status, reason = "VERIFIED_IN_HISTORY", "Existing verified original XBRL row"
                year_verified += 1
            elif len(paths) == 1:
                status, reason = "PENDING_INDEPENDENT_XBRL_REVIEW", "Index metadata is not audited XBRL fact evidence"
                year_candidates += 1
            elif len(paths) > 1:
                status, reason = "MULTIPLE_SOURCE_REVISIONS_NEED_REVIEW", "Distinct XBRL sources; verify revisions independently"
                year_candidates += 1
            else:
                status, reason = "OFFICIAL_INDEX_EXPORT_REQUIRED", "No uniquely supported audited consolidated annual XBRL candidate"
            filename, digest = sources.get(year, ("", ""))
            manifest.append({
                "Symbol": symbol, "Company": company, "Financial_Year": year,
                "Fiscal_Year_End": expected_end, "Recovery_Status": status,
                "Source_URL": "|".join(sorted(paths)) if key not in verified else "",
                "Official_Archive": ARCHIVE, "Index_File": filename,
                "Index_SHA256": digest, "Review_Reason": reason,
            })
        summary.append({
            "Financial_Year": year, "Tracked_Symbols": len(companies),
            "Existing_XBRL_Verified": year_verified, "Unverified_Index_Candidates": year_candidates,
            "Still_Need_Index_Or_Review": len(companies) - year_verified - year_candidates,
            "Official_Index_File_Present": year in sources,
            "Verification_Note": "CSV metadata never authorizes annual screen pass",
        })
    return manifest, summary


def write_csv(path: Path, rows: list[dict], fields: list[str] | tuple[str, ...]):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    manifest, summary = build(ROOT)
    write_csv(MANIFEST_FILE, manifest, MANIFEST_FIELDS)
    write_csv(SUMMARY_FILE, summary, list(summary[0]))
    for row in summary:
        print(
            f"Legacy FY {row['Financial_Year']}: tracked={row['Tracked_Symbols']}, "
            f"verified={row['Existing_XBRL_Verified']}, pending source review={row['Unverified_Index_Candidates']}, "
            f"missing index/review={row['Still_Need_Index_Or_Review']}"
        )
    print("Legacy index audit only; verified annual history and trade signals unchanged.")


if __name__ == "__main__":
    main()
