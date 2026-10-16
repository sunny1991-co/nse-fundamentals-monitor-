#!/usr/bin/env python3
"""Low-volume, fail-closed NSE *metadata-only* legacy annual index retrieval.

Uses the official NSE financial results index with two constrained publication
windows. The returned rows are untrusted filing-index candidates until the
underlying audited consolidated annual XBRL facts are verified independently.
Do not scrape around NSE access controls: stop on 403/429.
"""
from __future__ import annotations

from research_monitor.paths import data_root
import csv
import time
from datetime import date, datetime
from pathlib import Path

import requests

ROOT = data_root()
LANDING_URL = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
API_URL = "https://www.nseindia.com/api/corporates-financial-results"
STATUS_FILE = ROOT / "legacy_index_fetch_status.csv"
PERIODS = {
    "2022-23": ("01-04-2023", "31-03-2024"),
    "2023-24": ("01-04-2024", "31-03-2025"),
}
FIELDS = (
    "Symbol", "Company Name", "Period Ended", "Audited / Unaudited",
    "Consolidated / Non-Consolidated", "Period", "XBRL URL", "Broadcast Date",
)


def list_rows(payload):
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "records"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
    raise ValueError("NSE financial results index response has an unsupported structure")


def official_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": LANDING_URL,
        "X-Requested-With": "XMLHttpRequest",
    })
    landing = session.get(LANDING_URL, timeout=20)
    landing.raise_for_status()
    return session


def fetch_index(session, start, end):
    response = session.get(
        API_URL,
        params={"index": "equities", "period": "Annual", "from_date": start, "to_date": end},
        timeout=35,
    )
    response.raise_for_status()
    return list_rows(response.json())


def write_rows(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def as_csv_row(row):
    return {
        "Symbol": str(row.get("symbol", "") or "").strip().upper(),
        "Company Name": str(row.get("companyName", "") or "").strip(),
        "Period Ended": str(row.get("toDate", "") or "").strip(),
        "Audited / Unaudited": str(row.get("audited", "") or "").strip(),
        "Consolidated / Non-Consolidated": str(row.get("consolidated", "") or "").strip(),
        "Period": str(row.get("period", "") or "").strip(),
        "XBRL URL": str(row.get("xbrl", "") or "").strip(),
        "Broadcast Date": str(row.get("broadCastDate", "") or "").strip(),
    }


def collect(root=ROOT, session_factory=official_session, fetch=fetch_index):
    status = []
    session = None
    # Keep successful source files until a user explicitly replaces them; the
    # same historical filing index need not be downloaded every day.
    for year, (start, end) in PERIODS.items():
        output = root / f"nse_legacy_financial_results_{year}.csv"
        record = {
            "Financial_Year": year, "Checked_At": date.today().isoformat(),
            "Index_Status": "", "Source_Records": 0,
            "Error": "", "Official_Source": LANDING_URL,
        }
        if output.exists() and output.stat().st_size > 100:
            record["Index_Status"] = "EXISTING_EXPORT_PRESERVED"
            status.append(record)
            continue
        try:
            if session is None:
                session = session_factory()
            records = fetch(session, start, end)
            # Never store empty/error HTML as a successful official archive.
            if not records:
                record["Index_Status"] = "EMPTY_OFFICIAL_API_RESULT"
            else:
                normalized = [as_csv_row(row) for row in records]
                normalized = [
                    row for row in normalized if row["Symbol"] and row["Period Ended"]
                ]
                if normalized:
                    write_rows(output, normalized, FIELDS)
                    record["Index_Status"] = "INDEX_METADATA_COLLECTED_NOT_VERIFIED"
                    record["Source_Records"] = len(normalized)
                else:
                    record["Index_Status"] = "NO_RECOGNIZED_OFFICIAL_ROWS"
        except requests.HTTPError as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            record["Index_Status"] = "OFFICIAL_NSE_ACCESS_BLOCKED" if code in (403, 429) else "OFFICIAL_NSE_HTTP_ERROR"
            record["Error"] = str(exc)[:230]
        except (requests.RequestException, ValueError) as exc:
            record["Index_Status"] = "OFFICIAL_NSE_INDEX_UNAVAILABLE"
            record["Error"] = str(exc)[:230]
        status.append(record)
        if record["Index_Status"] == "OFFICIAL_NSE_ACCESS_BLOCKED":
            # Do not retry once NSE has refused access.
            for pending_year in list(PERIODS)[len(status):]:
                status.append({
                    "Financial_Year": pending_year, "Checked_At": date.today().isoformat(),
                    "Index_Status": "NOT_ATTEMPTED_AFTER_403_429",
                    "Source_Records": 0, "Error": record["Error"],
                    "Official_Source": LANDING_URL,
                })
            break
        time.sleep(0.25)
    return status


def main():
    status = collect(ROOT)
    if not status:
        raise RuntimeError("Legacy filing metadata collection yielded no status.")
    fields = ("Financial_Year", "Checked_At", "Index_Status", "Source_Records", "Error", "Official_Source")
    write_rows(STATUS_FILE, status, fields)
    for row in status:
        print(f"Legacy index {row['Financial_Year']}: {row['Index_Status']} rows={row['Source_Records']}")
    print("Official filing index is NOT independently verified audited annual XBRL data.")


if __name__ == "__main__":
    main()

