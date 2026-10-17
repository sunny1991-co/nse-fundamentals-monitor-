"""Offline regressions for automatic all-EQ backfill and no-trade radar."""
from datetime import date,datetime,timezone
import csv
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch

import pandas as pd

from nse_mainboard_history_backfill import (
    collect, DATABASE, BackfillBlocked, open_db, store_day,
)
from nse_mainboard_technical_radar import scan,raw_technical,OUTPUT
from swing_nse_history import parse_bhavcopy
from nse_mainboard_discovery import CATALOG


class ResearchAutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.now=datetime(2026,10,17,8,tzinfo=timezone.utc)
        with (self.root/CATALOG).open("w",newline="") as stream:
            w=csv.DictWriter(stream,fieldnames=["Symbol","ISIN","Company","Baseline_Tracked"])
            w.writeheader()
            w.writerow(dict(Symbol="NEW",ISIN="INE001A01011",Company="New Research",Baseline_Tracked="false"))
        self.verify_backfill=patch("nse_mainboard_history_backfill.verified_discovered_symbols",
                                   return_value={"NEW"})
        self.verify_radar=patch("nse_mainboard_technical_radar.verified_discovered_symbols",
                                return_value={"NEW"})
        self.verify_backfill.start()
        self.verify_radar.start()
        self.addCleanup(self.verify_backfill.stop)
        self.addCleanup(self.verify_radar.stop)
        (self.root/"annual_historical_results.csv").write_text("Symbol,Financial_Year\n")
        (self.root/"swing_watchlist.json").write_text("KEEP_UNCHANGED")
        (self.root/"market_ohlcv.csv").write_text("KEEP_ADJUSTED_UNCHANGED")
        (self.root/"FY2425.csv").write_text("KEEP_FY24_UNCHANGED")

    def test_all_eq_parse_is_separate_from_filtered_original_collector(self):
        sample=(
            "SYMBOL,SERIES,DATE1,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,TTL_TRD_QNTY\n"
            "NEW,EQ,16-Oct-2026,100,102,99,101,1000\n"
            "PAST,EQ,16-Oct-2026,10,11,9,10,500\n"
            "SME,SM,16-Oct-2026,100,110,90,100,300\n"
        ).encode()
        full=parse_bhavcopy(sample,date(2026,10,16),"full",None)
        original=parse_bhavcopy(sample,date(2026,10,16),"full",{"NEW"})
        self.assertEqual({row[0] for row in full},{"NEW","PAST"})
        self.assertEqual({row[0] for row in original},{"NEW"})

    def test_backfill_caches_all_official_eq_no_trade_output(self):
        seen=[]
        def fake_fetch(session,day,symbols):
            self.assertIsNone(symbols)
            seen.append(day)
            raw=[
                ("NEW",day.isoformat(),100,102,99,101,5000),
                ("PAST",day.isoformat(),10,11,9,10,1000),
            ]
            return ("OK","https://nsearchives.nseindia.com/demo.csv","a"*64,raw,"")
        with patch("nse_mainboard_history_backfill.expected_archive_day",return_value=True):
            r=collect(self.root,start=date(2026,10,15),end=date(2026,10,16),
                max_days=2,session=Mock(),fetch=fake_fetch,now=self.now)
            self.assertEqual(r["archive_days_added_this_run"],2)
            self.assertEqual(r["unadjusted_eq_bars_added_or_replaced_this_run"],4)
            self.assertEqual(r["estimated_outstanding_expected_sessions"],0)
            collect(self.root,start=date(2026,10,15),end=date(2026,10,16),
                max_days=2,session=Mock(),fetch=fake_fetch,now=self.now)
        self.assertEqual(len(seen),2)
        with sqlite3.connect(self.root/DATABASE) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM unadjusted_eq_bars").fetchone()[0],4)
            self.assertEqual(db.execute("SELECT count(*) FROM source_days WHERE status='OK'").fetchone()[0],2)
        self.assertEqual((self.root/"FY2425.csv").read_text(),"KEEP_FY24_UNCHANGED")
        self.assertEqual((self.root/"market_ohlcv.csv").read_text(),"KEEP_ADJUSTED_UNCHANGED")
        self.assertEqual((self.root/"swing_watchlist.json").read_text(),"KEEP_UNCHANGED")

    def test_http_403_stops_without_retry_or_incorrect_completed_date(self):
        mock=Mock(return_value=("BLOCKED","https://nsearchives.nseindia.com/demo","",[],"HTTP 403"))
        with patch("nse_mainboard_history_backfill.expected_archive_day",return_value=True):
            report=collect(self.root,start=date(2026,10,15),end=date(2026,10,16),
                max_days=2,session=Mock(),fetch=mock,now=self.now)
        self.assertEqual(mock.call_count,1)
        self.assertTrue(report["source_blocked"])
        self.assertEqual(report["archive_days_added_this_run"],0)
        with sqlite3.connect(self.root/DATABASE) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM source_days").fetchone()[0],0)

    def test_untrusted_archive_receipt_is_blocked(self):
        with open_db(self.root/DATABASE) as db:
            with self.assertRaisesRegex(BackfillBlocked,"UNTRUSTED"):
                store_day(db,date(2026,10,16),"OK",
                          "https://bad.example/day","a"*64,
                          [("NEW","2026-10-16",10,11,9,10,100)],"")
            self.assertEqual(db.execute("SELECT count(*) FROM source_days").fetchone()[0],0)

    def synthetic(self,volume=1_000_000):
        dates=pd.bdate_range(end="2026-10-16",periods=270)
        result=[]
        for i,dt in enumerate(dates):
            close=100*(1.0015**i)
            v=volume*1.5 if i==len(dates)-1 else volume
            result.append((dt.date().isoformat(),close*.995,close*1.002,close*.99,close,v))
        return result

    def test_raw_research_signal_never_becomes_trade(self):
        technical=raw_technical(self.synthetic(),self.now)
        self.assertEqual(technical["Technical_Research_Pattern"],
                         "UNADJUSTED_BREAKOUT_REVIEW_ONLY")
        with open_db(self.root/DATABASE) as db:
            db.executemany(
                """INSERT INTO unadjusted_eq_bars
                   (symbol,day,open,high,low,close,volume,source_url,source_sha256)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                [("NEW",*b,"https://nsearchives.nseindia.com/demo","f"*64)
                 for b in self.synthetic()],
            )
            db.commit()
        report=scan(self.root,now=self.now)
        self.assertEqual(report["unadjusted_breakout_research_candidates"],1)
        with (self.root/OUTPUT).open() as f:
            rec=list(csv.DictReader(f))[0]
        self.assertEqual(rec["Fundamental_Research_State"],
                         "MISSING_FOUR_FULLY_VERIFIED_ANNUAL_YEARS")
        self.assertEqual(rec["Trade_Eligible"],"false")
        self.assertEqual((self.root/"swing_watchlist.json").read_text(),"KEEP_UNCHANGED")
        self.assertEqual((self.root/"market_ohlcv.csv").read_text(),"KEEP_ADJUSTED_UNCHANGED")
        self.assertEqual(report["orders"],"DISABLED")

    def test_insufficient_technical_history_stays_pending(self):
        self.assertEqual(raw_technical(self.synthetic()[:150],self.now)[
            "Technical_Research_Pattern"],"UNADJUSTED_HISTORY_INSUFFICIENT")


if __name__=="__main__":
    unittest.main()
