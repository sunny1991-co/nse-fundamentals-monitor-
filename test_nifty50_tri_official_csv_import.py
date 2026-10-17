"""Synthetic offline CSV staging/review tests; never treat fixtures as NSE facts."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from nifty50_tri_official_csv_import import (
    STAGE_CSV, STAGE_RECEIPT, stage_official_csv, install_reviewed, read_staged,
    read_official_export, MIN_INSTALL_DAYS,
)
from nifty50_tri_official_import import OUTPUT_CSV, OUTPUT_META
from research_monitor.strategy_sources import read_nifty50_tri

NOW = datetime(2026,10,17,7,0,tzinfo=timezone.utc)


def sample(days=1265, *, price="20000", header="IndexName,Date,Total Returns Index,Net Total Return Index"):
    rows=[header]
    today=date(2026,10,16)
    dates=[]
    d=today
    while len(dates)<days:
        if d.weekday()<5:
            dates.append(d)
        d-=timedelta(days=1)
    for n,d in enumerate(sorted(dates)):
        rows.append(f"NIFTY 50,{d.strftime('%d %b %Y')},{Decimal(price)+n},100")
    return ("\n".join(rows)+"\n").encode()


class ManualOfficialCSVTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)

    def put(self, name, body):
        p=self.root/name
        p.write_bytes(body)
        return p

    def test_gross_csv_accepts_only_gross_column_and_exact_nifty50(self):
        got=read_official_export(sample(3),today=NOW.date())
        self.assertEqual(len(got),3)
        self.assertEqual(list(got.values())[0],"20000")
        self.assertNotIn("100",list(got.values()))
        for txt in (
            b"IndexName,Date,Close\nNIFTY 50,16 Oct 2026,25000\n",
            b"IndexName,Date,Net Total Return Index\nNIFTY 50,16 Oct 2026,24000\n",
            b"IndexName,Date,Total Returns Index\nNIFTY 500,16 Oct 2026,30000\n",
        ):
            with self.assertRaises(ValueError):
                read_official_export(txt,today=NOW.date())

    def test_one_official_file_stages_unreviewed_then_requires_owner_install(self):
        path=self.put("manual_official_source.csv",sample())
        result=stage_official_csv(self.root,path,now=NOW)
        self.assertEqual(result["state"],"OFFICIAL_CSV_STAGED_UNREVIEWED")
        self.assertGreaterEqual(result["staged_daily_rows"],MIN_INSTALL_DAYS)
        self.assertFalse(result["operator_source_checked"])
        self.assertFalse((self.root/OUTPUT_CSV).exists())
        self.assertFalse((self.root/OUTPUT_META).exists())
        receipt=json.loads((self.root/STAGE_RECEIPT).read_text())
        self.assertFalse(receipt["downloaded_from_official_site_confirmed_by_owner"])
        final=install_reviewed(self.root,now=NOW)
        self.assertEqual(final["state"],
                         "OWNER_REVIEWED_MANUAL_OFFICIAL_GROSS_TRI_INSTALLED_RESEARCH_ONLY")
        levels,digest=read_nifty50_tri(self.root)
        self.assertEqual(len(levels),1265)
        self.assertTrue(digest)
        self.assertEqual(levels.iloc[0],20000.)
        self.assertFalse(json.loads((self.root/OUTPUT_META).read_text())[
            "source_is_independently_audited"
        ])
        with self.assertRaisesRegex(ValueError,"REFUSE_OVERWRITE"):
            install_reviewed(self.root,now=NOW)

    def test_two_different_official_csv_exports_can_be_merged(self):
        body=sample()
        parts=body.decode().splitlines()
        header=parts[0]
        a=(header+"\n"+"\n".join(parts[1:640])+"\n").encode()
        b=(header+"\n"+"\n".join(parts[640:])+"\n").encode()
        first=self.put("year_1.csv",a)
        second=self.put("year_2.csv",b)
        x=stage_official_csv(self.root,first,now=NOW)
        self.assertFalse(x["enough_rows_for_review"])
        with self.assertRaisesRegex(ValueError,"INSUFFICIENT_1260"):
            install_reviewed(self.root,now=NOW)
        y=stage_official_csv(self.root,second,now=NOW)
        self.assertEqual(y["source_files_staged"],2)
        self.assertEqual(y["staged_daily_rows"],1265)
        self.assertEqual(len(read_staged(self.root)[0]),1265)

    def test_conflicting_overlap_rejected_without_touching_prior_staged(self):
        first=self.put("original.csv",sample(100))
        stage_official_csv(self.root,first,now=NOW)
        raw_before=(self.root/STAGE_CSV).read_bytes()
        receipt_before=(self.root/STAGE_RECEIPT).read_bytes()
        altered=sample(100,price="99999")
        second=self.put("other.csv",altered)
        with self.assertRaisesRegex(ValueError,"OVERLAPPING_SOURCE_TRI_VALUES_CONFLICT"):
            stage_official_csv(self.root,second,now=NOW)
        self.assertEqual((self.root/STAGE_CSV).read_bytes(),raw_before)
        self.assertEqual((self.root/STAGE_RECEIPT).read_bytes(),receipt_before)

    def test_invalid_original_spot_price_html_nan_or_future_rejected(self):
        invalid=[
            b"<html>Not an export</html>",
            b"IndexName,Date,Total Returns Index\nNIFTY 50,16 Oct 2026,NaN\n",
            b"IndexName,Date,Total Returns Index\nNIFTY 50,16 Oct 2030,22000\n",
            b"IndexName,Date,Total Returns Index\nNIFTY 50,16 Oct 2026,22000\nNIFTY 50,16 Oct 2026,22000\n",
        ]
        for file in invalid:
            with self.subTest(file=file[:20]):
                with self.assertRaises(ValueError):
                    read_official_export(file,today=NOW.date())

    def test_candidate_integrity_checksum_required(self):
        p=self.put("original.csv",sample())
        stage_official_csv(self.root,p,now=NOW)
        (self.root/STAGE_CSV).write_bytes(b"Date,Close\n2026-10-16,99999\n")
        with self.assertRaisesRegex(ValueError,"STAGED_TRI_INTEGRITY_INVALID"):
            install_reviewed(self.root,now=NOW)
        self.assertFalse((self.root/OUTPUT_CSV).exists())

    def test_duplicate_source_cannot_cause_extra_provenance(self):
        p=self.put("original.csv",sample(40))
        stage_official_csv(self.root,p,now=NOW)
        with self.assertRaisesRegex(ValueError,"SOURCE_FILE_ALREADY_IMPORTED"):
            stage_official_csv(self.root,p,now=NOW)

    def test_importer_never_creates_order_or_approves_data(self):
        p=self.put("original.csv",sample(40))
        state=stage_official_csv(self.root,p,now=NOW)
        self.assertEqual(state["trading_orders"],"DISABLED")
        self.assertFalse(state["operator_source_checked"])
        self.assertFalse((self.root/"annual_historical_results.csv").exists())
        self.assertFalse((self.root/"nse_sector_map.csv").exists())


if __name__=="__main__":
    unittest.main()
