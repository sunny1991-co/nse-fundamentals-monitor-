"""Offline tests for the curated annual PDF intake; no live download."""
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from nse_annual_report_pdf_pilot import (
    REL_FOLDER, SOURCES, SYMBOL, EXPECTED_ISIN,
    PdfPilotUnavailable, inspect_bytes, capture, safe_preset,
)
from nse_mainboard_discovery import CATALOG


class FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class FakeReader:
    def __init__(self, stream, strict=True):
        assert stream.read(5) == b"%PDF-"
        self.is_encrypted = False
        self.pages = [FakePage("20 MICRONS Annual Report 2022-23")]
        self.pages += [FakePage("Board and Directors") for _ in range(9)]
        self.pages += [
            FakePage(
                "Independent Auditors Report on Consolidated Financial Statements "
                "Consolidated Balance Sheet "
                "Consolidated Statement of Profit and Loss "
                "Consolidated Cash Flow Statement"
            ),
            FakePage("Notes to Consolidated Financial Statements"),
        ]


class FakeReaderFY24(FakeReader):
    def __init__(self, stream, strict=True):
        super().__init__(stream, strict=strict)
        self.pages[0] = FakePage("20 MICRONS Annual Report FY 2023-24")


def pdf_bytes():
    return b"%PDF-1.5\n" + b"x" * 3500 + b"\n%%EOF\n"


class PdfPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 17, 9, tzinfo=timezone.utc)
        with (self.root/CATALOG).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["Symbol", "ISIN"])
            writer.writeheader()
            writer.writerow({"Symbol": SYMBOL, "ISIN": EXPECTED_ISIN})
        (self.root/"annual_historical_results.csv").write_text("SAVED VERIFIED DATA")
        self.identity = patch(
            "nse_annual_report_pdf_pilot.verified_discovered_symbols",
            return_value={SYMBOL},
        )
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def test_pdf_review_sections_never_validate_financial_facts(self):
        r = inspect_bytes(pdf_bytes(), "2022-23", reader_cls=FakeReader)
        self.assertEqual(r["pdf_pages"], 12)
        self.assertTrue(r["all_required_sections_located"])
        self.assertTrue(r["candidate_section_pages_1_based"]["consolidated_balance_sheet"])
        self.assertEqual(r["verified_financial_facts"], 0)
        self.assertEqual(r["reporting_basis_auditor_and_units_review"], "PENDING_MANUAL_REVIEW")

    def test_year_mismatch_rejected(self):
        with self.assertRaisesRegex(PdfPilotUnavailable, "COVER_FY"):
            inspect_bytes(pdf_bytes(), "2023-24", reader_cls=FakeReader)

    def test_arbitrary_pdf_source_is_refused_before_download(self):
        with self.assertRaisesRegex(PdfPilotUnavailable, "UNREVIEWED"):
            safe_preset("2022-23", "https://example.com/file.pdf")

    def test_non_pdf_payload_refused(self):
        with self.assertRaisesRegex(PdfPilotUnavailable, "INVALID_OR_TRUNCATED"):
            inspect_bytes(b"<html>" + b"x"*3500, "2022-23", reader_cls=FakeReader)

    def test_source_downloads_once_and_cache_is_checked(self):
        session = Mock()
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [pdf_bytes()]
        session.get.return_value = response
        original = (self.root/"annual_historical_results.csv").read_bytes()

        first = capture(self.root, ["2022-23"], now=self.now,
                        session=session, reader_cls=FakeReader)
        self.assertEqual(first["reports"][0]["pdf_sha256"], hashlib.sha256(pdf_bytes()).hexdigest())
        self.assertEqual(first["verified_financial_facts_added"], 0)
        self.assertFalse(first["annual_historical_results_modified"])
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(original, (self.root/"annual_historical_results.csv").read_bytes())

        second = capture(self.root, ["2022-23"], now=self.now,
                         session=session, reader_cls=FakeReader)
        self.assertTrue(second["reports"][0]["cached_file_reused"])
        self.assertEqual(session.get.call_count, 1)

    def test_cached_source_modified_is_not_used(self):
        session = Mock()
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [pdf_bytes()]
        session.get.return_value = response
        capture(self.root, ["2022-23"], now=self.now,
                session=session, reader_cls=FakeReader)
        report_file = self.root/REL_FOLDER/"20MICRONS_FY2022_23.pdf"
        report_file.write_bytes(pdf_bytes() + b"invalid")
        with self.assertRaisesRegex(PdfPilotUnavailable, "INTEGRITY_FAILED"):
            capture(self.root, ["2022-23"], now=self.now,
                    session=session, reader_cls=FakeReader)

    def test_isin_not_cross_verified_blocks_capture(self):
        with (self.root/CATALOG).open("w", newline="") as handle:
            w = csv.DictWriter(handle, fieldnames=["Symbol", "ISIN"])
            w.writeheader()
            w.writerow({"Symbol": SYMBOL, "ISIN": "INE999A01099"})
        with self.assertRaisesRegex(PdfPilotUnavailable, "ISIN_NOT_MATCHING"):
            capture(self.root, ["2022-23"], now=self.now, reader_cls=FakeReader)

    def test_both_years_must_be_explicitly_recognized(self):
        with self.assertRaisesRegex(PdfPilotUnavailable, "YEAR_SELECTION"):
            capture(self.root, ["2022-23", "2022-23"], now=self.now, reader_cls=FakeReader)
        self.assertEqual(len(SOURCES), 2)
        r = inspect_bytes(pdf_bytes(), "2023-24", reader_cls=FakeReaderFY24)
        self.assertEqual(r["verified_financial_facts"], 0)

    def test_source_denial_403_is_not_retried(self):
        session = Mock()
        response = Mock(status_code=403)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        session.get.return_value = response
        with self.assertRaisesRegex(PdfPilotUnavailable, "SOURCE_HTTP_403"):
            capture(self.root, ["2022-23"], now=self.now,
                    session=session, reader_cls=FakeReader)
        self.assertEqual(session.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
