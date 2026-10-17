#!/usr/bin/env python3
"""Bounded FY23/FY24 company-published Annual Report PDF intake and page review.

PDF text and dates are EVIDENCE CANDIDATES ONLY, never source-verified
annual XBRL, exact numeric facts, an audited annual fundamental PASS, a
tradable stock, or an order. The two source URLs are exact links observed on
20 Microns' official investor-relations annual reports page, not guessed URLs.
Does not read broker tokens or modify existing annual/strategy CSVs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import requests

from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status
from research_monitor.discovered_annual_universe import verified_discovered_symbols
from nse_mainboard_discovery import CATALOG

ORIGIN = "https://www.20microns.com/financial-report"
PDF_HOST = "fb949d44010d40db156a-660543cdef2fb4867335bf5294dccba6.ssl.cf2.rackcdn.com"
SOURCES = {
    "2022-23": "https://" + PDF_HOST + "/financial_report/1689584643.pdf",
    "2023-24": "https://" + PDF_HOST + "/financial_report/1719920352.pdf",
}
SYMBOL = "20MICRONS"
EXPECTED_ISIN = "INE144J01027"
PDF_PREFIX = b"%PDF-"
MIN_SIZE = 3000
MAX_BYTES = 32 * 1024 * 1024
MAX_PAGES = 400
MIN_PAGES = 10
MIN_YEAR_MATCH_PAGES = 1
REL_FOLDER = Path("runtime") / "annual_report_pdf_pilot"
STATUS_NAME = "annual_report_pdf_pilot_status.json"
ANNUAL_YEAR_MARKERS = {"2022-23": "2022 23", "2023-24": "2023 24"}
MARKERS = {
    "consolidated_auditor": (
        re.compile(r"independent\s+auditors?(?:['\u2019]s?)?\s+report", re.I),
        re.compile(r"consolidated", re.I),
    ),
    "consolidated_balance_sheet": (
        re.compile(r"consolidated\s+balance\s+sheet", re.I),
    ),
    "consolidated_profit_loss": (
        re.compile(r"consolidated\s+(?:statement\s+of\s+)?profit\s+and\s+loss", re.I),
    ),
    "consolidated_cash_flow": (
        re.compile(r"consolidated\s+(?:statement\s+of\s+)?cash\s+flow", re.I),
    ),
}


class PdfPilotUnavailable(RuntimeError):
    pass


def verified_listing(root: Path, *, now=None) -> None:
    """Require a current official catalog and the expected unique symbol+ISIN."""
    current = verified_discovered_symbols(root, now=now)
    if SYMBOL not in current:
        raise PdfPilotUnavailable("COMPANY_NOT_IN_CURRENT_VERIFIED_NSE_EQ_UNIVERSE")
    with (Path(root) / CATALOG).open(newline="", encoding="utf-8-sig") as stream:
        import csv
        rows = [r for r in csv.DictReader(stream) if r.get("Symbol") == SYMBOL]
    if len(rows) != 1 or rows[0].get("ISIN") != EXPECTED_ISIN:
        raise PdfPilotUnavailable("COMPANY_ISIN_NOT_MATCHING_VERIFIED_NSE_EQ")


def safe_preset(year: str, url: str) -> None:
    if year not in SOURCES or url != SOURCES[year]:
        raise PdfPilotUnavailable("UNREVIEWED_ANNUAL_REPORT_SOURCE")
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != PDF_HOST or parts.port is not None
        or parts.username or parts.password or parts.query or parts.fragment):
        raise PdfPilotUnavailable("PDF_SOURCE_HOST_NOT_REVIEWED")


def limited_download(session: requests.Session, url: str) -> bytes:
    with session.get(
        url, timeout=(15, 60), allow_redirects=False, stream=True,
        headers={"User-Agent": "NSE-mainboard-public-annual-pdf-research/1.0",
                 "Accept": "application/pdf"},
    ) as resp:
        if resp.status_code != 200:
            raise PdfPilotUnavailable("ANNUAL_PDF_SOURCE_HTTP_" + str(resp.status_code))
        chunks, size = [], 0
        for chunk in resp.iter_content(65536):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_BYTES:
                raise PdfPilotUnavailable("ANNUAL_PDF_EXCEEDS_MAX_BYTES")
            chunks.append(chunk)
    return b"".join(chunks)


def validate_pdf(payload: bytes) -> None:
    if (not isinstance(payload, bytes) or
        not MIN_SIZE <= len(payload) <= MAX_BYTES or
        not payload.startswith(PDF_PREFIX) or
        b"%%EOF" not in payload[-2048:]):
        raise PdfPilotUnavailable("ANNUAL_PDF_INVALID_OR_TRUNCATED")


def page_markers(reader, year: str) -> dict:
    """Non-authoritative table-of-contents/section locator only."""
    total = len(reader.pages)
    if not MIN_PAGES <= total <= MAX_PAGES:
        raise PdfPilotUnavailable("ANNUAL_PDF_IMPLAUSIBLE_PAGE_COUNT")
    # Text extractors may lose words or pages. Never mark a PDF financial row
    # as VERIFIED because a keyword appeared.
    first = []
    for i in range(min(4, total)):
        first.append(reader.pages[i].extract_text() or "")
    cover = " ".join(first).upper()
    cover = re.sub(r"[^0-9A-Z]+", " ", cover)
    expected = ANNUAL_YEAR_MARKERS[year]
    if expected not in cover and expected.replace(" ", "") not in cover:
        raise PdfPilotUnavailable("ANNUAL_REPORT_COVER_FY_NOT_FOUND")
    found = {name: [] for name in MARKERS}
    evidence = {}
    for i in range(max(5, total // 3), total):
        page = reader.pages[i]
        text = page.extract_text() or ""
        folded = re.sub(r"\s+", " ", text)
        for name, rules in MARKERS.items():
            if all(rule.search(folded) for rule in rules):
                if len(found[name]) >= 12:
                    continue
                found[name].append(i + 1)  # 1-based physical PDF page
                evidence[f"{name}_{i+1}"] = folded[:240]
    return {
        "pdf_pages": total,
        "cover_year_text_found": True,
        "candidate_section_pages_1_based": found,
        "section_locator_excerpt_not_fact": evidence,
        "all_required_sections_located": all(found.values()),
        "machine_extracted_financial_facts": 0,
        "verified_financial_facts": 0,
        "reporting_basis_auditor_and_units_review": "PENDING_MANUAL_REVIEW",
    }


def inspect_bytes(payload: bytes, year: str, reader_cls=None) -> dict:
    validate_pdf(payload)
    if reader_cls is None:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise PdfPilotUnavailable("PYPDF_REQUIRED_PIP_INSTALL_PYPDF") from exc
        reader_cls = PdfReader
    try:
        reader = reader_cls(io.BytesIO(payload), strict=True)
        if getattr(reader, "is_encrypted", False):
            raise PdfPilotUnavailable("ENCRYPTED_ANNUAL_REPORT_NOT_SUPPORTED")
        result = page_markers(reader, year)
    except PdfPilotUnavailable:
        raise
    except (ValueError, TypeError, KeyError, IndexError, RuntimeError) as exc:
        raise PdfPilotUnavailable("ANNUAL_REPORT_PDF_PARSE_FAILED") from exc
    return result


def atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def capture(root: Path, years: list[str], *, session=None,
            now: datetime | None = None, reader_cls=None) -> dict:
    """Two bounded publicly linked PDFs, source+byte attested, REVIEW only."""
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_AWARE_CLOCK_REQUIRED")
    if not years or len(years) > 2 or len(set(years)) != len(years):
        raise PdfPilotUnavailable("YEAR_SELECTION_1_TO_2_UNIQUE")
    for year in years:
        safe_preset(year, SOURCES.get(year, ""))
    verified_listing(root, now=now)
    output_dir = root / REL_FOLDER
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    own = session is None
    session = session or requests.Session()
    report = {
        "checked_at_utc": now.astimezone(timezone.utc).isoformat(),
        "state": "SOURCE_EVIDENCE_CAPTURE_REVIEW_ONLY",
        "scope": "CURATED_COMPANY_INVESTOR_ANNUAL_PDF_NO_FACT_APPROVAL",
        "company_symbol": SYMBOL,
        "company_isin": EXPECTED_ISIN,
        "source_page": ORIGIN,
        "reports": [],
        "verified_financial_facts_added": 0,
        "annual_historical_results_modified": False,
        "strict_fundamental_screen_promoted": False,
        "orders": "DISABLED",
    }
    try:
        for year in years:
            url = SOURCES[year]
            path = output_dir / f"{SYMBOL}_FY{year.replace('-', '_')}.pdf"
            meta_path = path.with_suffix(".json")
            source_was_cached = path.is_file()
            if source_was_cached:
                payload = path.read_bytes()
                if not meta_path.is_file():
                    raise PdfPilotUnavailable("CACHED_PDF_MISSING_PRIOR_SOURCE_RECEIPT")
                previous = json.loads(meta_path.read_text(encoding="utf-8"))
                if (previous.get("source_url") != url or
                    previous.get("pdf_sha256") != hashlib.sha256(payload).hexdigest()):
                    raise PdfPilotUnavailable("CACHED_PDF_INTEGRITY_FAILED")
            else:
                payload = limited_download(session, url)
            sections = inspect_bytes(payload, year, reader_cls=reader_cls)
            digest = hashlib.sha256(payload).hexdigest()
            receipt = {
                "symbol": SYMBOL, "isin": EXPECTED_ISIN,
                "financial_year": year,
                "source_page": ORIGIN, "source_url": url,
                "pdf_sha256": digest, "pdf_bytes": len(payload),
                "cached_file_reused": source_was_cached,
                "download_and_scan_at_utc": now.astimezone(timezone.utc).isoformat(),
                "review_status": "CANDIDATE_PDF_SECTIONS_ONLY_NOT_FINANCIAL_VERIFICATION",
                "trade_eligible": False,
                **sections,
            }
            # PDF and its attestation receipt live only under the private
            # persistent runtime path; never publish downloaded report to git.
            if not source_was_cached:
                atomic_bytes(path, payload)
            atomic_status(meta_path, receipt)
            report["reports"].append(receipt)
    finally:
        if own:
            session.close()
    atomic_status(root / "runtime" / STATUS_NAME, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", choices=[*SOURCES, "both"], default="both")
    args = parser.parse_args()
    years = list(SOURCES) if args.year == "both" else [args.year]
    try:
        result = capture(data_root(), years)
    except (PdfPilotUnavailable, requests.RequestException, OSError, ValueError) as exc:
        result = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "state": "ANNUAL_PDF_PILOT_BLOCKED",
            "reason": str(exc)[:140], "orders": "DISABLED",
            "source_evidence_not_promoted": True,
        }
        atomic_status(data_root() / "runtime" / STATUS_NAME, result)
        print(json.dumps(result, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
