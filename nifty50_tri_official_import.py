"""Fetch ORIGINAL NIFTY 50 *gross TRI* to a quarantine CSV, no auto attestation.

Source: NSE Indices' public historical total-return index report.
Official page: https://www.niftyindices.com/reports/historical-data
Transport endpoint: POST /Backpage.aspx/getTotalReturnIndexString

A successful HTTPS response does not by itself constitute human review or
independent audit. This program never puts a fetched series into backtests
automatically. After examining its provenance and the index values on NSE
Indices, an owner may explicitly pass --install-reviewed to attest it.

No data is manufactured, forward-filled, replaced with spot NIFTY, obtained
from another vendor, or submitted to any broker. No order endpoints.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

SOURCE_PAGE = "https://www.niftyindices.com/reports/historical-data"
ENDPOINT = "https://www.niftyindices.com/Backpage.aspx/getTotalReturnIndexString"
INDEX_NAME = "NIFTY 50"
MIN_ROWS = 1260
MAX_ROWS = 4500
MAX_RESPONSE_BYTES = 6_000_000
QUERY_START = date(2017, 1, 1)
CANDIDATE_CSV = "nifty50_tri_download_candidate.csv"
CANDIDATE_RECEIPT = "nifty50_tri_download_receipt.json"
OUTPUT_CSV = "nifty50_tri_history.csv"
OUTPUT_META = "nifty50_tri_history_metadata.json"
STATUS_JSON = "runtime/nifty50_tri_acquisition_status.json"
IST = ZoneInfo("Asia/Kolkata")


def response_details(response):
    """Return safe bounded HTTP diagnostics, never arbitrary response content.

    Do not follow server redirects or expose server-provided URL/query values.
    A 3xx / noncanonical response remains untrusted even on NSE's domain.
    """
    code = getattr(response, "status_code", None)
    if type(code) is not int or not 100 <= code <= 599:
        code = None
    details = {"http_status": code}
    actual = getattr(response, "url", None)
    if isinstance(actual, str):
        details["response_url_matches_official_endpoint"] = actual == ENDPOINT
    else:
        details["response_url_matches_official_endpoint"] = False
    if code is not None and 300 <= code < 400:
        location = getattr(response, "headers", {}).get("Location", "")
        if not isinstance(location, str) or not location:
            details["redirect_class"] = "LOCATION_NOT_PROVIDED"
        else:
            target = urlsplit(location)
            if not target.hostname:
                details["redirect_class"] = "RELATIVE_REDIRECT_NOT_FOLLOWED"
            elif target.scheme == "https" and target.hostname == "www.niftyindices.com":
                details["redirect_class"] = "OFFICIAL_HOST_REDIRECT_NOT_FOLLOWED"
            else:
                details["redirect_class"] = "OTHER_REDIRECT_NOT_FOLLOWED"
    return details


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_rows(body: bytes, *, today: date):
    """Exact index, unique dates, finite strictly positive *gross TRI* levels."""
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_RESPONSE_BYTES:
        raise ValueError("OFFICIAL_TRI_RESPONSE_SIZE_INVALID")
    if b"<!DOCTYPE" in body.upper() or body.lstrip().lower().startswith(b"<html"):
        raise ValueError("OFFICIAL_TRI_NOT_JSON")
    try:
        outer = json.loads(body)
        data = json.loads(outer["d"])
    except (ValueError, TypeError, KeyError):
        raise ValueError("OFFICIAL_TRI_WRAPPER_INVALID") from None
    if not isinstance(data, list) or not MIN_ROWS <= len(data) <= MAX_ROWS:
        raise ValueError("OFFICIAL_TRI_NUMBER_OF_ROWS_INVALID")
    dates = {}
    for row in data:
        if not isinstance(row, dict) or "NTR_Value" not in row:
            raise ValueError("OFFICIAL_TRI_SCHEMA_MISMATCH")
        if str(row.get("Index Name", "")).strip().upper() != INDEX_NAME:
            raise ValueError("WRONG_INDEX_NOT_NIFTY_50")
        raw_day = str(row.get("Date", "")).strip()
        try:
            day = datetime.strptime(raw_day, "%d %b %Y").date()
        except ValueError:
            raise ValueError("OFFICIAL_TRI_DATE_INVALID") from None
        if day < QUERY_START or day > today or day in dates:
            raise ValueError("OFFICIAL_TRI_DATE_DUPLICATE_OR_FUTURE")
        # A total-return benchmark is not a price benchmark nor NTR.
        try:
            number = Decimal(str(row["TotalReturnsIndex"]).replace(",", ""))
        except (KeyError, InvalidOperation, TypeError):
            raise ValueError("OFFICIAL_GROSS_TRI_LEVEL_INVALID") from None
        if not number.is_finite() or number <= 0 or number > 10**9:
            raise ValueError("OFFICIAL_GROSS_TRI_LEVEL_INVALID")
        dates[day] = format(number, "f")
    if today - max(dates) > timedelta(days=45):
        raise ValueError("OFFICIAL_TRI_MOST_RECENT_DATE_TOO_OLD")
    rows = [{"Date": d.isoformat(), "Close": dates[d]} for d in sorted(dates)]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=("Date", "Close"), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8"), rows


def atomic_bytes(path: Path, raw: bytes):
    """Atomic single-file replacement; integrity validated via SHA256 contract."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False, prefix=".tri_") as out:
            temp = Path(out.name)
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, 0o600)
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def fetch_candidate(root, *, now=None, session=None):
    """One bounded official POST; status says UNREVIEWED even after fetch succeeds."""
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    today = now.astimezone(IST).date()
    result = {
        "scope": "OFFICIAL_NIFTY_50_GROSS_TRI_UNREVIEWED_DOWNLOAD",
        "state": "DOWNLOAD_FAILED_OR_UNAVAILABLE",
        "updated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "source_page": SOURCE_PAGE,
        "source_url": ENDPOINT,
        "operator_source_checked": False,
        "promoted_to_verified_benchmark": False,
        "trading_orders": "DISABLED",
    }
    own = session is None
    session = session or requests.Session()
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Android; Mobile) NSE-index-integrity-checker/1.0",
            "Referer": SOURCE_PAGE,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        }
        request = {
            "cinfo": ("{'name':'NIFTY 50',"
                      f"'startDate':'{QUERY_START.strftime('%d-%b-%Y')}',"
                      f"'endDate':'{today.strftime('%d-%b-%Y')}',"
                      "'indexName':'NIFTY 50'}")
        }
        response = session.post(
            ENDPOINT, json=request, headers=headers,
            timeout=75, allow_redirects=False,
        )
        result.update(response_details(response))
        if response.status_code in (403, 429):
            result["state"] = f"OFFICIAL_ACCESS_BLOCKED_{response.status_code}"
        elif 300 <= response.status_code < 400:
            result["state"] = "OFFICIAL_REDIRECT_NOT_FOLLOWED"
        elif response.status_code != 200:
            result["state"] = "OFFICIAL_HTTP_ERROR_UNVERIFIED"
        elif response.url != ENDPOINT:
            result["state"] = "OFFICIAL_RESPONSE_URL_MISMATCH_UNVERIFIED"
        else:
            csv_bytes, rows = canonical_rows(response.content, today=today)
            receipt = {
                "scope": "UNREVIEWED_OFFICIAL_GROSS_TRI_CANDIDATE_ONLY",
                "source_page": SOURCE_PAGE,
                "source_url": ENDPOINT,
                "first_date": rows[0]["Date"],
                "last_date": rows[-1]["Date"],
                "daily_rows": len(rows),
                "gross_tri_csv_sha256": sha(csv_bytes),
                "source_response_sha256": sha(response.content),
                "fetched_at_utc": now.astimezone(timezone.utc).isoformat(),
                "operator_source_checked": False,
                "live_eligible": False,
                "orders": "DISABLED",
            }
            atomic_bytes(root/CANDIDATE_CSV, csv_bytes)
            atomic_bytes(
                root/CANDIDATE_RECEIPT,
                (json.dumps(receipt, indent=2)+"\n").encode("utf-8"),
            )
            result.update(
                state="OFFICIAL_CANDIDATE_READY_UNREVIEWED",
                candidate_rows=len(rows),
                candidate_first_date=rows[0]["Date"],
                candidate_last_date=rows[-1]["Date"],
                candidate_sha256=sha(csv_bytes),
            )
    except requests.RequestException:
        result["state"] = "OFFICIAL_SOURCE_NETWORK_UNAVAILABLE"
    except (ValueError, TypeError, OSError) as exc:
        code = str(exc)
        allowed = {
            "OFFICIAL_TRI_RESPONSE_SIZE_INVALID",
            "OFFICIAL_TRI_NOT_JSON",
            "OFFICIAL_TRI_WRAPPER_INVALID",
            "OFFICIAL_TRI_NUMBER_OF_ROWS_INVALID",
            "OFFICIAL_TRI_SCHEMA_MISMATCH",
            "WRONG_INDEX_NOT_NIFTY_50",
            "OFFICIAL_TRI_DATE_INVALID",
            "OFFICIAL_TRI_DATE_DUPLICATE_OR_FUTURE",
            "OFFICIAL_GROSS_TRI_LEVEL_INVALID",
            "OFFICIAL_TRI_MOST_RECENT_DATE_TOO_OLD",
        }
        result["state"] = "OFFICIAL_CANDIDATE_REJECTED"
        result["reason_code"] = code if code in allowed else "OFFICIAL_RESPONSE_INVALID"
    finally:
        if own:
            session.close()
        atomic_status(root/STATUS_JSON, result)
    return result


def install_reviewed(root, *, now=None):
    """Only an explicit owner's source-review action may attest the source."""
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    staged, receipt = root/CANDIDATE_CSV, root/CANDIDATE_RECEIPT
    if not staged.is_file() or not receipt.is_file():
        raise ValueError("NO_OFFICIAL_CANDIDATE_TO_REVIEW")
    try:
        record = json.loads(receipt.read_text(encoding="utf-8"))
        payload = staged.read_bytes()
        if (record.get("scope") != "UNREVIEWED_OFFICIAL_GROSS_TRI_CANDIDATE_ONLY"
                or record.get("source_url") != ENDPOINT
                or record.get("gross_tri_csv_sha256") != sha(payload)
                or record.get("operator_source_checked") is not False):
            raise ValueError("CANDIDATE_RECEIPT_OR_HASH_MISMATCH")
    except (OSError, ValueError, TypeError):
        raise ValueError("CANDIDATE_RECEIPT_OR_HASH_MISMATCH") from None
    # Revalidate candidate content without trusting the unreviewed receipt's row count.
    try:
        parsed = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
        if not MIN_ROWS <= len(parsed) <= MAX_ROWS:
            raise ValueError()
        dates = [date.fromisoformat(x["Date"]) for x in parsed]
        levels = [Decimal(x["Close"]) for x in parsed]
        if (
            sorted(dates) != dates or len(dates) != len(set(dates))
            or dates[0] < QUERY_START or dates[-1] > now.astimezone(IST).date()
            or any(not n.is_finite() or n <= 0 for n in levels)
            or parsed[0]["Date"] != record.get("first_date")
            or parsed[-1]["Date"] != record.get("last_date")
            or len(parsed) != record.get("daily_rows")
        ):
            raise ValueError()
    except (KeyError, UnicodeError, ValueError, InvalidOperation, TypeError):
        raise ValueError("REVIEWED_TRI_CSV_INVALID") from None
    dest, meta = root/OUTPUT_CSV, root/OUTPUT_META
    if dest.exists() or meta.exists():
        raise ValueError("BENCHMARK_ALREADY_INSTALLED_REFUSE_OVERWRITE")
    metadata = {
        "type": "OFFICIAL_NIFTY_50_TRI",
        "index_name": INDEX_NAME+" TRI",
        "official_source_url": SOURCE_PAGE,
        "official_data_endpoint": ENDPOINT,
        "sha256": sha(payload),
        "source_response_sha256": record["source_response_sha256"],
        "operator_source_checked": True,
        "source_review": "EXPLICIT_OWNER_REVIEW_CONFIRMATION",
        "reviewed_at_utc": now.astimezone(timezone.utc).isoformat(),
        "source_is_independently_audited": False,
        "orders": "DISABLED",
    }
    # Metadata last: loader refuses a half-installed or hash-mismatched pair.
    atomic_bytes(dest, payload)
    atomic_bytes(
        meta, (json.dumps(metadata, indent=2)+"\n").encode("utf-8")
    )
    result = {
        "state": "OPERATOR_REVIEWED_TRI_INSTALLED_RESEARCH_ONLY",
        "installed_daily_rows": len(parsed),
        "sha256": sha(payload),
        "source_is_independently_audited": False,
        "strategy_validated": False,
        "orders": "DISABLED",
    }
    atomic_status(root/STATUS_JSON, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument(
        "--fetch", action="store_true",
        help="Download official raw gross TRI into unreviewed quarantine only",
    )
    operation.add_argument(
        "--install-reviewed", action="store_true",
        help="ATTEST that YOU checked the NSE Indices source; install candidate",
    )
    args = parser.parse_args()
    try:
        report = (fetch_candidate(data_root()) if args.fetch
                  else install_reviewed(data_root()))
    except (OSError, ValueError) as exc:
        code = str(exc)
        allow = {
            "NO_OFFICIAL_CANDIDATE_TO_REVIEW",
            "CANDIDATE_RECEIPT_OR_HASH_MISMATCH",
            "REVIEWED_TRI_CSV_INVALID",
            "BENCHMARK_ALREADY_INSTALLED_REFUSE_OVERWRITE",
        }
        report = {
            "state": "OFFICIAL_TRI_IMPORT_NOT_COMPLETED",
            "reason_code": code if code in allow else "TRI_IMPORT_ERROR",
            "orders": "DISABLED",
        }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
