"""Source-safe independent NSE Indices reference collection tests."""
from datetime import datetime, timezone
import csv
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from nse_index_industry_reference import (
    SOURCE_URL, REFERENCE_NAME, STATUS_NAME, TRUST_SCOPE,
    collect, parse_constituents,
)


STAMP = "2026-10-17T03:15:00+00:00"
TEST_CSV = (
    b"Company Name,Industry,Symbol,Series,ISIN Code\n"
    b"Example Industries,Capital Goods,SYNTH,EQ,INE123456789\n"
    b"Demo Finance,Financial Services,FINDEMO,EQ,INE123456780\n"
)


def response(data=TEST_CSV, status=200, url=SOURCE_URL):
    return SimpleNamespace(content=data, status_code=status, url=url)


class NSEIndexReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026,10,17,3,15,tzinfo=timezone.utc)

    def test_official_source_provenance_reference_only(self):
        calls=[]
        def fetch(url, **kwargs):
            calls.append((url,kwargs))
            return response()
        got=collect(self.root,self.now,fetch=fetch)
        self.assertEqual(got["state"],"OFFICIAL_COARSE_REFERENCE_UPDATED")
        self.assertEqual(got["reference_rows_this_run"],2)
        self.assertEqual(calls[0][0],SOURCE_URL)
        self.assertFalse(calls[0][1]["allow_redirects"])
        self.assertTrue((self.root/"runtime"/STATUS_NAME).is_file())
        with (self.root/REFERENCE_NAME).open(newline="") as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual([r["Symbol"] for r in rows],["FINDEMO","SYNTH"])
        self.assertEqual(rows[0]["Trust_Scope"],TRUST_SCOPE)
        self.assertNotIn("Lookup_Status",rows[0])
        self.assertFalse((self.root/"nse_sector_map.csv").exists())
        self.assertEqual(got["strict_fundamental_pass_changes"],0)
        self.assertEqual(got["live_orders"],"DISABLED")

    def test_nse_403_preserves_last_verified_index_reference(self):
        collect(self.root,self.now,fetch=lambda *a,**k:response())
        original=(self.root/REFERENCE_NAME).read_bytes()
        status=collect(self.root,self.now,fetch=lambda *a,**k:response(
            data=b"<html>denied</html>",status=403
        ))
        self.assertEqual(status["state"],"BLOCKED_HTTP_403")
        self.assertEqual((self.root/REFERENCE_NAME).read_bytes(),original)
        self.assertEqual(status["reference_rows_this_run"],0)

    def test_missing_schema_and_html_cannot_replace_reference(self):
        collect(self.root,self.now,fetch=lambda *a,**k:response())
        original=(self.root/REFERENCE_NAME).read_bytes()
        for body in (
            b"Company Name,Symbol\nA,TEST\n",
            b"<html>403 Forbidden</html>",
            b"Company Name,Industry,Symbol\nA,B,A\nC,D,A\n",
            b"Company Name,Industry,Symbol\nA,B,TEST!\n",
            b"Company Name,Industry,Symbol\nA,B,TEST\\x00\n",
        ):
            status=collect(self.root,self.now,fetch=lambda *a,**k:response(body))
            self.assertEqual(status["state"],"SOURCE_OR_SCHEMA_INVALID")
            self.assertEqual((self.root/REFERENCE_NAME).read_bytes(),original)

    def test_redirect_or_unexpected_http_is_not_trusted(self):
        for status_code,target in (
            (302,SOURCE_URL),
            (200,"https://example.invalid/other.csv"),
            (503,SOURCE_URL),
        ):
            status=collect(self.root,self.now,fetch=lambda *a,**k:response(
                status=status_code,url=target
            ))
            self.assertEqual(status["state"],"SOURCE_HTTP_OR_REDIRECT_UNVERIFIED")
            self.assertFalse((self.root/REFERENCE_NAME).exists())

    def test_parser_rejects_malformed_inputs_and_missing_industry(self):
        for body in (
            b"",
            b"A,B,C\n1,2,3\n",
            b"Company Name,Industry,Symbol\nDemo,,TEST\n",
            b"Company Name,Industry,Symbol\nDemo,Industry,TEST,EXTRA\n",
        ):
            with self.assertRaises(ValueError):
                parse_constituents(body,stamp=STAMP)


if __name__=="__main__":
    unittest.main()
