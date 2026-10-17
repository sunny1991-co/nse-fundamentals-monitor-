"""Synthetic transport/CSV test only, NOT real official data verification."""
from datetime import datetime, date, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from nifty50_tri_official_import import (
    CANDIDATE_CSV, CANDIDATE_RECEIPT, ENDPOINT, OUTPUT_CSV, OUTPUT_META,
    SOURCE_PAGE, canonical_rows, fetch_candidate, install_reviewed,
)
from research_monitor.strategy_sources import read_nifty50_tri

NOW = datetime(2026,10,17,6,0,tzinfo=timezone.utc)


def fixture(rows=1300, *, tamper=None):
    days=[]
    day=date(2026,10,16)
    while len(days)<rows:
        if day.weekday() < 5:
            days.append(day)
        day-=timedelta(days=1)
    days.reverse()
    payload=[]
    for i, day in enumerate(days):
        payload.append({
            "Index Name":"Nifty 50",
            "Date":day.strftime("%d %b %Y"),
            "TotalReturnsIndex":str(24000+i*1.1),
            "NTR_Value":str(22000+i*1.0),
        })
    if tamper:
        payload[0].update(tamper)
    return json.dumps({"d":json.dumps(payload)}).encode()


def response(body=None, *, code=200, url=ENDPOINT):
    return SimpleNamespace(
        status_code=code, url=url,
        content=fixture() if body is None else body,
    )


class OfficialGrossTRIImportTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)

    def fetch(self, response_object=None):
        transport=Mock()
        transport.post.return_value = response_object or response()
        result=fetch_candidate(self.root, now=NOW, session=transport)
        return result, transport

    def test_schema_verified_unreviewed_download_never_auto_promotes(self):
        result, client = self.fetch()
        self.assertEqual(result["state"],"OFFICIAL_CANDIDATE_READY_UNREVIEWED")
        self.assertEqual(result["candidate_rows"],1300)
        self.assertFalse(result["operator_source_checked"])
        self.assertFalse(result["promoted_to_verified_benchmark"])
        self.assertFalse((self.root/OUTPUT_CSV).exists())
        self.assertFalse((self.root/OUTPUT_META).exists())
        self.assertTrue((self.root/CANDIDATE_CSV).exists())
        self.assertTrue((self.root/CANDIDATE_RECEIPT).exists())
        kwargs=client.post.call_args.kwargs
        self.assertEqual(client.post.call_args.args[0],ENDPOINT)
        self.assertEqual(kwargs["allow_redirects"],False)
        self.assertIn("getTotalReturnIndexString",ENDPOINT)
        self.assertIn("startDate",kwargs["json"]["cinfo"])
        self.assertIn("NIFTY 50",kwargs["json"]["cinfo"])
        self.assertEqual(
            client.post.call_args.kwargs["headers"]["Referer"],SOURCE_PAGE
        )

    def test_gross_tri_is_selected_not_net_tri(self):
        payload, rows=canonical_rows(fixture(),today=NOW.date())
        self.assertEqual(rows[0]["Close"],"24000.0")
        self.assertNotEqual(rows[0]["Close"],"22000.0")
        self.assertTrue(payload.startswith(b"Date,Close\n"))

    def test_official_403_and_redirect_fail_shut_no_leak(self):
        for original in (
            response(code=403),
            response(code=429),
            response(code=200,url="https://untrusted.example.org/tri"),
            response(code=302),
        ):
            with self.subTest(code=original.status_code,url=original.url):
                result,_=self.fetch(original)
                self.assertNotEqual(result["state"],"OFFICIAL_CANDIDATE_READY_UNREVIEWED")
                self.assertFalse((self.root/OUTPUT_META).exists())
                self.assertFalse((self.root/CANDIDATE_CSV).exists())

    def test_bad_source_does_not_overwrite_prior_good_candidate(self):
        accepted,_=self.fetch()
        original=(self.root/CANDIDATE_CSV).read_bytes()
        for bad in (
            b"<html>403</html>",
            b"",
            b'{"d": "[]"}',
            fixture(tamper={"Index Name":"Nifty Price Only"}),
            fixture(tamper={"TotalReturnsIndex":"NaN"}),
            fixture(tamper={"TotalReturnsIndex":"0"}),
            fixture(tamper={"Date":"16 Oct 2099"}),
        ):
            with self.subTest(payload_sha=hashlib.sha256(bad).hexdigest()):
                result,_=self.fetch(response(body=bad))
                self.assertEqual(result["state"],"OFFICIAL_CANDIDATE_REJECTED")
                self.assertEqual((self.root/CANDIDATE_CSV).read_bytes(),original)

    def test_no_automatic_source_attestation_review_is_explicit(self):
        with self.assertRaisesRegex(ValueError,"NO_OFFICIAL_CANDIDATE_TO_REVIEW"):
            install_reviewed(self.root,now=NOW)
        self.fetch()
        report=install_reviewed(self.root,now=NOW)
        self.assertEqual(report["state"],"OPERATOR_REVIEWED_TRI_INSTALLED_RESEARCH_ONLY")
        self.assertFalse(report["source_is_independently_audited"])
        self.assertEqual(report["orders"],"DISABLED")
        levels,digest=read_nifty50_tri(self.root)
        self.assertEqual(len(levels),1300)
        self.assertEqual(digest,hashlib.sha256((self.root/OUTPUT_CSV).read_bytes()).hexdigest())
        metadata=json.loads((self.root/OUTPUT_META).read_text())
        self.assertTrue(metadata["operator_source_checked"])
        self.assertFalse(metadata["source_is_independently_audited"])
        self.assertEqual(metadata["official_source_url"],SOURCE_PAGE)
        with self.assertRaisesRegex(ValueError,"REFUSE_OVERWRITE"):
            install_reviewed(self.root,now=NOW)

    def test_changed_candidate_after_fetch_refuses_review(self):
        self.fetch()
        (self.root/CANDIDATE_CSV).write_text("Date,Close\n2026-10-16,30000\n")
        with self.assertRaisesRegex(ValueError,"CANDIDATE_RECEIPT_OR_HASH_MISMATCH"):
            install_reviewed(self.root,now=NOW)
        self.assertFalse((self.root/OUTPUT_CSV).exists())

    def test_receipt_forged_operator_checked_refuses(self):
        self.fetch()
        receipt_path=self.root/CANDIDATE_RECEIPT
        record=json.loads(receipt_path.read_text())
        record["operator_source_checked"]=True
        receipt_path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError,"CANDIDATE_RECEIPT_OR_HASH_MISMATCH"):
            install_reviewed(self.root,now=NOW)

    def test_invalid_dates_and_too_little_history_rejected(self):
        with self.assertRaisesRegex(ValueError,"NUMBER_OF_ROWS"):
            canonical_rows(fixture(100),today=NOW.date())
        bad=fixture(tamper={"Date":"16 Oct 1999"})
        with self.assertRaisesRegex(ValueError,"DUPLICATE_OR_FUTURE"):
            canonical_rows(bad,today=NOW.date())


if __name__ == "__main__":
    unittest.main()
