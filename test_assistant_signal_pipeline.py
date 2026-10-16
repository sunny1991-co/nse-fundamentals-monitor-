"""Strict-source paper-only swing assistant watchlist and quote transition tests."""
from __future__ import annotations

from datetime import datetime,timedelta,timezone
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from research_monitor.assistant_signal_pipeline import (
    SCOPE,scan,match_quote_and_prepare_paper_prompt,
    advance_approved_paper_positions,
)
from research_monitor.assistant_trade_queue import ApprovalQueue


NOW=datetime(2026,10,16,4,30,0,tzinfo=timezone.utc)


def test_bars():
    dates=pd.bdate_range(end="2026-10-15",periods=1260)
    return pd.DataFrame({
        "Symbol":["SYNTH"]*len(dates),"Date":dates,
        "Open":[99.]*len(dates),"High":[102.]*len(dates),
        "Low":[98.]*len(dates),"Close":[100.]*len(dates),
        "Volume":[2000000]*len(dates),
    })


def qualifying_features(bars,_benchmark):
    d=bars.reset_index(drop=True).copy()
    d["Signal"]=False
    d.loc[len(d)-1,"Signal"]=True
    d["Stop"]=95.
    d["Relative_TRI_63d_Return"]=.18
    return d


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_sources(root,now=NOW,eligible=True):
    root=Path(root)
    (root/"market_ohlcv.csv").write_text("LOCAL_SYNTHETIC_OHLCV_HASH_ANCHOR")
    (root/"nifty50_tri_history.csv").write_text("LOCAL_SYNTHETIC_TRI_HASH_ANCHOR")
    (root/"final_fundamental_shortlist.csv").write_text(
        "Symbol,Final_Fundamental_Criteria_Pass\n"
        "SYNTH,"+("True" if eligible else "False")+"\n"
    )
    (root/"final_fundamental_screen_summary.csv").write_text(
        "Report_Generated_On,Final_Shortlist_Count\n"
        +now.date().isoformat()+","+("1" if eligible else "0")+"\n"
    )


def mock_history(root,today=None):
    return test_bars(),_sha(Path(root)/"market_ohlcv.csv")


def mock_benchmark(root):
    series=pd.Series([100.,102.],index=pd.DatetimeIndex([
        "2026-10-14","2026-10-15",
    ]))
    return series,_sha(Path(root)/"nifty50_tri_history.csv")


def update_quotes(root,now,bid=100.4,ask=100.5,ltp=100.45,
                  status="LIVE_MONITORING",feed_status="POLLING_10_SECONDS"):
    runtime=Path(root)/"runtime"
    runtime.mkdir(parents=True,exist_ok=True)
    (runtime/"worker_status.json").write_text(json.dumps({
        "phase":status,"live_feed":feed_status,
        "updated_at":now.isoformat(),
    }))
    (runtime/"quotes.json").write_text(json.dumps({
        "fetched_at":now.isoformat(),
        "quotes":[{
            "symbol":"SYNTH","token":"NSE_EQ|INE123456789",
            "exchange":"NSE","feed_time":now.isoformat(),
            "bid":bid,"ask":ask,"ltp":ltp,"validation_errors":[],
        }],
    }))


class SwingAssistantSignalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def scan(self):
        return scan(self.root,NOW,history_loader=mock_history,
                    benchmark_loader=mock_benchmark,
                    feature_builder=qualifying_features)

    def test_no_strict_fundamental_eligibility_means_no_buy_approval(self):
        make_sources(self.root,eligible=False)
        result=self.scan()
        self.assertEqual(result["state"],"NO_FULL_STRICT_FUNDAMENTAL_PASS")
        self.assertEqual(result["watchlist_count"],0)
        self.assertFalse(
            (self.root/"runtime"/"assistant_research_watchlist.json").exists()
        )
        self.assertEqual(result["trade_orders"],"DISABLED")

    def test_missing_source_fails_closed_and_deletes_obsolete_watchlist(self):
        make_sources(self.root)
        self.scan()
        self.assertTrue(
            (self.root/"runtime"/"assistant_research_watchlist.json").exists()
        )
        (self.root/"final_fundamental_shortlist.csv").unlink()
        status=self.scan()
        self.assertEqual(status["state"],"BLOCKED_SOURCE_REQUIRED")
        self.assertFalse(
            (self.root/"runtime"/"assistant_research_watchlist.json").exists()
        )

    def test_strict_source_and_matching_live_zone_prompts_only_one_paper_trade(self):
        make_sources(self.root)
        report=self.scan()
        self.assertEqual(report["scope"],SCOPE)
        self.assertEqual(report["state"],"RESEARCH_WATCHLIST_READY_PAPER_ONLY")
        self.assertEqual(report["watchlist_count"],1)
        watch=json.loads(
            (self.root/"runtime"/"assistant_research_watchlist.json").read_text()
        )
        self.assertEqual(watch["entries"][0]["symbol"],"SYNTH")
        self.assertEqual(watch["entries"][0]["entry_min"],100.)
        self.assertEqual(watch["entries"][0]["stop"],95.)
        self.assertFalse(watch["eligible_for_live_orders"])

        update_quotes(self.root,NOW)
        result=match_quote_and_prepare_paper_prompt(self.root,NOW)
        self.assertEqual(len(result["owner_prompts"]),1)
        self.assertEqual(result["live_orders"],0)
        book=ApprovalQueue(
            self.root/"runtime"/"assistant_trade_queue.sqlite3"
        )
        trade_id=result["owner_prompts"][0]["id"]
        proposal=book.get(trade_id)
        self.assertEqual(proposal["state"],"WAITING_FOR_OWNER")
        duplicate=match_quote_and_prepare_paper_prompt(self.root,NOW)
        self.assertEqual(duplicate["owner_prompts"][0]["id"],trade_id)
        self.assertEqual(len(book.list_recent()),1)

        book.decide(trade_id,proposal["fingerprint"],
                    "yes",2,now=NOW.timestamp()+1)
        t2=NOW+timedelta(seconds=2)
        update_quotes(self.root,t2)
        filled=advance_approved_paper_positions(self.root,t2)
        self.assertEqual(filled["updated_positions"],1)
        self.assertEqual(book.get(trade_id)["state"],"OPEN_PAPER_ONLY")
        self.assertEqual(book.get(trade_id)["quantity"],2)

        t3=NOW+timedelta(seconds=3)
        update_quotes(self.root,t3,bid=94,ask=94.25,ltp=94.1)
        closed=advance_approved_paper_positions(self.root,t3)
        self.assertEqual(closed["updated_positions"],1)
        self.assertEqual(book.get(trade_id)["exit_reason"],"STOP_OR_GAP")
        self.assertLess(book.get(trade_id)["exit"],95)
        self.assertEqual(book.get(trade_id)["shares_sold"],2)
        self.assertEqual(closed["orders"],"DISABLED")
        self.assertEqual(closed["automated_broker_exits"],"NOT_IMPLEMENTED")

    def test_quote_spread_or_missing_market_data_never_proposes(self):
        make_sources(self.root)
        self.scan()
        blocked=match_quote_and_prepare_paper_prompt(self.root,NOW)
        self.assertEqual(blocked["owner_prompts"],[])
        self.assertEqual(blocked["live_orders"],0)
        update_quotes(self.root,NOW,bid=97,ask=101)
        blocked=match_quote_and_prepare_paper_prompt(self.root,NOW)
        self.assertEqual(blocked["owner_prompts"],[])
        self.assertFalse(
            (self.root/"runtime"/"assistant_trade_queue.sqlite3").exists()
        )

    def test_changed_source_hash_prevents_unreviewed_proposal(self):
        make_sources(self.root)
        self.scan()
        update_quotes(self.root,NOW)
        (self.root/"market_ohlcv.csv").write_text("SYNTHETIC_PRICE_CHANGED")
        blocked=match_quote_and_prepare_paper_prompt(self.root,NOW)
        self.assertEqual(blocked["owner_prompts"],[])
        self.assertFalse(
            (self.root/"runtime"/"assistant_trade_queue.sqlite3").exists()
        )

    def test_missing_feed_never_assumes_stop_was_filled(self):
        make_sources(self.root)
        self.scan()
        update_quotes(self.root,NOW)
        pending=match_quote_and_prepare_paper_prompt(self.root,NOW)
        book=ApprovalQueue(
            self.root/"runtime"/"assistant_trade_queue.sqlite3"
        )
        row=book.get(pending["owner_prompts"][0]["id"])
        book.decide(row["id"],row["fingerprint"],"yes",2,
                    now=NOW.timestamp()+1)
        after=advance_approved_paper_positions(
            self.root,NOW+timedelta(seconds=99)
        )
        self.assertEqual(after["updated_positions"],0)
        self.assertEqual(
            after["state"],"PAPER_QUOTES_MISSING_OR_STALE_NO_FILL_ASSUMED"
        )
        self.assertEqual(book.get(row["id"])["state"],"ARMED_PAPER_ONLY")


if __name__=="__main__":
    unittest.main()
