"""Causal, source-attested NSE fast-track EOD study: never broker execution."""
from __future__ import annotations
import csv
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

import fast_track_two_year_swing_research as fast
from nse_mainboard_discovery import CATALOG

NOW=datetime(2026,10,17,16,50,tzinfo=timezone.utc)
SYMS=("SYNTHA","SYNTHB","SYNTHC")


def write_csv(path,columns,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


class FastTrackResearchTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.protected={
            "final_fundamental_shortlist.csv":b"STRICT_UNMODIFIED",
            "annual_historical_results.csv":b"ORIGINAL_AUDITED_XBRL_UNMODIFIED",
            "swing_watchlist.json":b"ORDERS_DISABLED",
        }
        for k,v in self.protected.items():
            (self.root/k).write_bytes(v)
        write_csv(self.root/CATALOG,
            ["Symbol","ISIN","Company","Series"],[
                {"Symbol":s,"ISIN":f"INE{i:09d}","Company":s+" Limited","Series":"EQ"}
                for i,s in enumerate(SYMS)])
        write_csv(self.root/"annual_fundamental_quality_report.csv",
            ["Symbol","Two_Year_Baseline_Data_Quality"],[
                {"Symbol":s,"Two_Year_Baseline_Data_Quality":"PASS"} for s in SYMS])
        write_csv(self.root/"fast_fundamental_shortlist.csv",
            ["Symbol","Fast_Fundamental_Pass","Research_Scope",
             "Revenue_Growth_Pct","Net_Profit_Growth_Pct","EPS_Growth_Pct"],[
                 {"Symbol":s,"Fast_Fundamental_Pass":"True",
                  "Research_Scope":"PROVISIONAL_LOWER_CONFIDENCE_RESEARCH_ONLY",
                  "Revenue_Growth_Pct":"12","Net_Profit_Growth_Pct":"20",
                  "EPS_Growth_Pct":"15"} for s in SYMS])
        write_csv(self.root/"fast_fundamental_screen_summary.csv",
            ["Report_Generated_On","Fast_Fundamental_Pass_Count","Status"],[
                {"Report_Generated_On":"2026-10-17",
                 "Fast_Fundamental_Pass_Count":len(SYMS),
                 "Status":"PROVISIONAL_SCREEN_COMPLETE"}])
        for name in ("FY2425.csv","NSE_FY2025-26_Consolidated_Fundamentals.csv"):
            (self.root/name).write_text("NEVER_REWRITE",encoding="utf-8")
        dates=pd.bdate_range(end="2026-10-16",periods=320)
        rows=[]
        for s in SYMS:
            for j,date in enumerate(dates):
                close=100+j*.02
                rows.append({"Symbol":s,"Date":date.strftime("%Y-%m-%d"),
                             "Open":close,"High":close+2,"Low":close-2,
                             "Close":close,"Volume":1_000_000})
        write_csv(self.root/"market_ohlcv.csv",
            ["Symbol","Date","Open","High","Low","Close","Volume"],rows)
        sha=hashlib.sha256((self.root/"market_ohlcv.csv").read_bytes()).hexdigest()
        (self.root/"market_data_metadata.json").write_text(json.dumps({
            "sha256":sha,
            "corporate_actions_adjusted":True,
            "source":"SYNTHETIC_TEST_ATTESTATION_ONLY",
            "adjustment_method":"MOCK_TEST",
        }))
        (self.root/"nifty50_tri_history.csv").write_text("TEST_ONLY")
        (self.root/"nifty50_tri_history_metadata.json").write_text("TEST_ONLY")
        self.identity=patch.object(fast,"verified_discovered_symbols",
                                   return_value=set(SYMS))
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def assert_protected(self):
        for k,v in self.protected.items():
            self.assertEqual((self.root/k).read_bytes(),v)

    @staticmethod
    def fake_features(bars,benchmark):
        self_bars=bars.copy()
        self_bars["Signal"]=False
        self_bars.loc[self_bars.index[-1],"Signal"]=True
        self_bars["Stop"]=self_bars["Close"]*.95
        self_bars["Relative_TRI_63d_Return"]=.15
        return self_bars

    def invoke(self,cap=3,feature_builder=None):
        return fast.scan(
            self.root,now=NOW,max_symbols=cap,
            benchmark_loader=lambda _root:(
                pd.Series([100.],index=pd.DatetimeIndex(["2026-10-16"])),
                "ab"*32),
            feature_builder=feature_builder or self.fake_features,
            resource_probe=lambda:{"heavy_research_allowed":True,"tier":"MEDIUM"},
        )

    def test_valid_two_fy_and_adjusted_history_returns_only_research(self):
        status=self.invoke()
        self.assertEqual(status["state"],"FAST_TRACK_PROVISIONAL_EOD_RESEARCH_UPDATED")
        self.assertEqual(status["bounded_symbols_assessed_this_run"],3)
        self.assertEqual(status["research_patterns_this_run"],3)
        self.assertEqual(status["research_rows_written"],3)
        with (self.root/fast.OUTPUT).open(newline="") as f:
            candidates=list(csv.DictReader(f))
        self.assertEqual(len(candidates),3)
        for row in candidates:
            self.assertEqual(row["Research_Scope"],
                             "PROVISIONAL_TWO_FY_EOD_ONLY_NO_TRADE_ALERT")
            self.assertEqual(row["Paper_Trade_Eligible"],"false")
            self.assertEqual(row["Live_Trade_Eligible"],"false")
            self.assertEqual(row["Orders"],"DISABLED")
            self.assertEqual(row["Backtested_Win_Probability"],"")
            self.assertEqual(row["Signal_Date"],"2026-10-16")
            self.assertGreater(float(row["Indicative_Target_2R"]),
                               float(row["EOD_Reference_Close"]))
        self.assertFalse((self.root/"runtime"/"assistant_trade_queue.sqlite3").exists())
        self.assert_protected()

    def test_daily_small_batches_rotate_without_expanding_memory(self):
        one=self.invoke(cap=1)
        two=self.invoke(cap=1)
        three=self.invoke(cap=1)
        four=self.invoke(cap=1)
        self.assertEqual([x["research_batch_start_offset"]
                          for x in (one,two,three,four)],[0,1,2,0])
        self.assertEqual([x["research_batch_next_offset"]
                          for x in (one,two,three,four)],[1,2,0,1])
        self.assertFalse(one["coverage_is_full_provisional_universe"])
        self.assertTrue(three["universe_rotation_complete_this_run"])
        self.assert_protected()

    def test_adjustment_attestation_mismatch_blocks_without_trades(self):
        (self.root/"market_data_metadata.json").write_text(json.dumps({
            "sha256":"a"*64,"corporate_actions_adjusted":True,
            "source":"TEST","adjustment_method":"TEST"
        }))
        with self.assertRaisesRegex(fast.FastTrackBlocked,"ATTESTATION_INVALID"):
            self.invoke()
        self.assertFalse((self.root/"runtime"/"assistant_trade_queue.sqlite3").exists())
        self.assert_protected()

    def test_provisional_shortlist_not_allowed_without_two_year_quality(self):
        p=self.root/"annual_fundamental_quality_report.csv"
        write_csv(p,["Symbol","Two_Year_Baseline_Data_Quality"],[
            {"Symbol":s,"Two_Year_Baseline_Data_Quality":"FAIL"} for s in SYMS])
        with self.assertRaisesRegex(fast.FastTrackBlocked,
                                    "PROVISIONAL_AND_YEAR_QUALITY_DISAGREE"):
            self.invoke()
        self.assert_protected()

    def test_provisional_fast_screen_summary_disagreement_is_blocked(self):
        write_csv(self.root/"fast_fundamental_screen_summary.csv",
            ["Report_Generated_On","Fast_Fundamental_Pass_Count","Status"],[
                {"Report_Generated_On":"2026-10-17",
                 "Fast_Fundamental_Pass_Count":"99",
                 "Status":"PROVISIONAL_SCREEN_COMPLETE"}])
        with self.assertRaisesRegex(fast.FastTrackBlocked,
                                    "PROVISIONAL_SUMMARY_COUNT_MISMATCH"):
            self.invoke()
        self.assert_protected()

    def test_stale_completed_eod_cannot_make_fresh_pattern(self):
        path=self.root/"market_ohlcv.csv"
        lines=path.read_text().splitlines()
        # Move every source row one month into the past; hash correctly
        # so the only remaining gate is stale completed EOD chronology.
        modified=[]
        for line in lines:
            if line.startswith(("SYNTHA,","SYNTHB,","SYNTHC,")):
                fields=line.split(",")
                fields[1]=(pd.Timestamp(fields[1])-pd.Timedelta(days=31)).strftime("%Y-%m-%d")
                line=",".join(fields)
            modified.append(line)
        path.write_text("\n".join(modified)+"\n")
        meta=json.loads((self.root/"market_data_metadata.json").read_text())
        meta["sha256"]=hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root/"market_data_metadata.json").write_text(json.dumps(meta))
        report=self.invoke()
        self.assertEqual(report["research_patterns_this_run"],0)
        self.assertEqual(report["qualified_adjusted_price_histories"],0)
        self.assert_protected()

    def test_ram_pressure_prevents_analysis_without_unverified_output(self):
        with self.assertRaisesRegex(fast.FastTrackBlocked,"RAM_PRESSURE"):
            fast.scan(self.root,now=NOW,max_symbols=3,
                resource_probe=lambda:{"heavy_research_allowed":False})
        self.assertFalse((self.root/fast.OUTPUT).exists())
        self.assert_protected()


if __name__=="__main__":
    unittest.main()
