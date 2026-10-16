"""Local synthetic exact-session paper holding reconciliation, zero orders."""
from __future__ import annotations

from datetime import datetime,date,timedelta,timezone
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from research_monitor.assistant_eod_reconcile import (
    SCOPE,reconcile_paper_eod,
)
from research_monitor.assistant_trade_queue import ApprovalQueue


NOW=datetime(2026,10,21,12,0,tzinfo=timezone.utc)
ENTERED=datetime(2026,10,16,6,0,tzinfo=timezone.utc)


def prepare(root):
    root=Path(root)
    runtime=root/"runtime"
    runtime.mkdir(exist_ok=True)
    book=ApprovalQueue(runtime/"assistant_trade_queue.sqlite3")
    row=book.propose(
        symbol="SYNTH",signal_day="2026-10-15",
        entry_min=100,entry_max=101,stop=95,
        target_1=112,target_2=118,
        capital_inr=100000,source_sha="f"*64,signal_price=100.5,
        now=ENTERED.timestamp(),
    )
    book.decide(row["id"],row["fingerprint"],"yes",3,
                now=ENTERED.timestamp()+1)
    book.observe(row["id"],100.5,ENTERED.timestamp()+2,
                 now=ENTERED.timestamp()+2,verified_feed=True,
                 session_day="2026-10-16")
    return book,row["id"]


def calendar(root):
    items={}
    day=date(2026,10,17)
    while day<=date(2026,10,20):
        key=day.isoformat()
        sessions={}
        if day.weekday()<5:
            sessions[key]={
                "open":key+"T09:15:00+05:30",
                "close":key+"T15:30:00+05:30",
            }
        items[key]={
            "verified":True,
            "source":"OFFICIAL_NSE_REVIEWED_TEST_CALENDAR",
            "valid_from":"2026-01-01","valid_to":"2026-12-31",
            "sessions":sessions,
        }
        day+=timedelta(days=1)
    (Path(root)/"runtime"/"calendar_history.json").write_text(json.dumps(items))


def bars(*,omit=None,stop=False):
    dates=pd.bdate_range(end="2026-10-20",periods=300)
    frame=pd.DataFrame({
        "Symbol":["SYNTH"]*len(dates),"Date":dates,
        "Open":[100.]*len(dates),"High":[103.]*len(dates),
        "Low":[99.]*len(dates),"Close":[100.]*len(dates),
        "Volume":[2_000_000.]*len(dates),
    })
    if stop:
        idx=frame.Date.eq(pd.Timestamp("2026-10-19"))
        frame.loc[idx,["Open","High","Low","Close"]]=[91.,115.,90.,110.]
    if omit:
        frame=frame.loc[~frame.Date.eq(pd.Timestamp(omit))].reset_index(drop=True)
    return frame


def features(data):
    return data.reset_index(drop=True).assign(
        ATR=2.0,Signal=False
    )


class EODPaperTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.book,self.pid=prepare(self.root)
        calendar(self.root)

    def test_stop_first_daily_ohlc_with_gap_and_no_live_order(self):
        result=reconcile_paper_eod(
            self.root,NOW,
            history_loader=lambda _root,today=None:(bars(stop=True),"fake-sha"),
            feature_builder=features,
        )
        self.assertEqual(result["scope"],SCOPE)
        self.assertEqual(result["state"],"PAPER_EOD_RECONCILIATION_COMPLETE")
        self.assertEqual(result["completed_sessions_recorded"],1)
        self.assertEqual(result["paper_exits"],1)
        position=self.book.get(self.pid)
        self.assertEqual(position["state"],"CLOSED_PAPER_ONLY")
        self.assertEqual(position["exit_reason"],"EOD_GAP_OR_STOP_FIRST")
        self.assertAlmostEqual(position["exit"],91*.999)
        self.assertEqual(result["real_orders"],"DISABLED")
        again=reconcile_paper_eod(
            self.root,NOW,
            history_loader=lambda _root,today=None:(bars(stop=True),"fake-sha"),
            feature_builder=features,
        )
        self.assertEqual(again["paper_exits"],0)
        self.assertEqual(again["open_paper_positions_examined"],0)

    def test_missing_session_calendar_blocks_no_optimistic_position_exit(self):
        missing=json.loads(
            (self.root/"runtime"/"calendar_history.json").read_text()
        )
        del missing["2026-10-19"]
        (self.root/"runtime"/"calendar_history.json").write_text(
            json.dumps(missing)
        )
        output=reconcile_paper_eod(
            self.root,NOW,
            history_loader=lambda _root,today=None:(bars(stop=True),"fake-sha"),
            feature_builder=features,
        )
        self.assertEqual(output["blocked_positions"],1)
        self.assertEqual(output["paper_exits"],0)
        self.assertEqual(self.book.get(self.pid)["state"],"OPEN_PAPER_ONLY")

    def test_missing_completed_ohlcv_session_blocks_any_fabricated_fill(self):
        output=reconcile_paper_eod(
            self.root,NOW,
            history_loader=lambda _root,today=None:(
                bars(omit="2026-10-19",stop=True),"fake-sha"
            ),
            feature_builder=features,
        )
        self.assertEqual(output["blocked_positions"],1)
        self.assertEqual(output["completed_sessions_recorded"],0)
        self.assertEqual(self.book.get(self.pid)["shares_sold"],0)

    def test_100_completed_sessions_trigger_conservative_time_exit(self):
        dates=pd.bdate_range(start="2026-10-19",periods=100)
        last=None
        for timestamp in dates:
            last=self.book.complete_eod_session(
                self.pid,str(timestamp.date()),
                {"Open":102,"High":104,"Low":98,"Close":102},
                atr=2,source_verified=True
            )
        self.assertEqual(last["state"],"CLOSED_PAPER_ONLY")
        self.assertEqual(last["exit_reason"],"EOD_TIME_100_SESSIONS")
        self.assertEqual(last["completed_session_count"],100)
        self.assertEqual(last["shares_sold"],3)
        self.assertEqual(last["real_orders"],
                         "DISABLED_NO_VERIFIED_EXECUTION_ADAPTER")


if __name__=="__main__":
    unittest.main()
