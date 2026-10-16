"""Transactional and execution-boundary tests for owner YES/NO + quantity."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from research_monitor.assistant_trade_queue import (
    ApprovalQueue, MODE, STRATEGY, MAX_HOLD_SESSIONS,
)


class ApprovalAssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"runtime"/"assistant_trade_queue.sqlite3"
        self.book=ApprovalQueue(self.path)
        self.params=dict(
            symbol="SYNTH",signal_day="2026-10-16",
            entry_min=100,entry_max=101,stop=95,
            target_1=112,target_2=118,capital_inr=100_000,
            source_sha="a"*64,signal_price=100.5,
            now=1792110000,ttl=300,
        )
        # This POSIX timestamp is safely after the 2026-10-16 midnight.
        self.row=self.book.propose(**self.params)

    def test_yes_quantity_only_and_expiry_in_paper(self):
        self.assertEqual(self.row["state"],"WAITING_FOR_OWNER")
        self.assertEqual(self.row["plan"]["mode"],MODE)
        maximum=self.row["plan"]["maximum_approved_quantity"]
        self.assertGreater(maximum,2)
        with self.assertRaises(ValueError):
            self.book.decide(self.row["id"],self.row["fingerprint"],"yes",0,
                             now=self.params["now"]+1)
        with self.assertRaises(ValueError):
            self.book.decide(self.row["id"],self.row["fingerprint"],"yes",
                             maximum+1,now=self.params["now"]+1)
        with self.assertRaises(ValueError):
            self.book.decide(self.row["id"],"wrong","yes",1,
                             now=self.params["now"]+1)
        approved=self.book.decide(
            self.row["id"],self.row["fingerprint"],"yes",3,
            now=self.params["now"]+1
        )
        self.assertEqual(approved["quantity"],3)
        self.assertEqual(approved["state"],"ARMED_PAPER_ONLY")
        self.assertFalse(approved["live_trade_authorized"])
        self.assertEqual(approved["real_orders"],
                         "DISABLED_NO_VERIFIED_EXECUTION_ADAPTER")
        with self.assertRaises(ValueError):
            self.book.decide(self.row["id"],self.row["fingerprint"],"yes",3,
                             now=self.params["now"]+2)

    def test_reject_no_does_not_require_quantity(self):
        rejected=self.book.decide(self.row["id"],self.row["fingerprint"],
                                  "no",now=self.params["now"]+1)
        self.assertEqual(rejected["state"],"REJECTED")
        self.assertIsNone(rejected["quantity"])
        with self.assertRaisesRegex(ValueError,"NOT_ARMED_OR_OPEN"):
            self.book.observe(rejected["id"],100,self.params["now"]+2,
                              now=self.params["now"]+2,verified_feed=True)

    def test_stale_plan_expires_without_broker_side_effect(self):
        expired=self.book.decide(
            self.row["id"],self.row["fingerprint"],"yes",1,
            now=self.params["now"]+301
        )
        self.assertEqual(expired["state"],"EXPIRED")
        self.assertIsNone(expired["quantity"])

    def test_quote_range_entry_no_chase_stop_gap_and_restart_durable(self):
        pid=self.row["id"]
        t=self.params["now"]
        self.book.decide(pid,self.row["fingerprint"],"yes",5,now=t+1)
        initial=self.book.observe(
            pid,103,t+2,now=t+2,verified_feed=True)
        self.assertEqual(initial["state"],"ARMED_PAPER_ONLY")
        entered=self.book.observe(
            pid,100.75,t+3,now=t+3,verified_feed=True,
            session_day="2026-10-16")
        self.assertEqual(entered["state"],"OPEN_PAPER_ONLY")
        self.assertEqual(entered["shares_remaining"],5)
        restarted=ApprovalQueue(self.path)
        closed=restarted.observe(
            pid,89,t+4,now=t+4,verified_feed=True)
        self.assertEqual(closed["state"],"CLOSED_PAPER_ONLY")
        self.assertEqual(closed["exit"],89)
        self.assertEqual(closed["exit_reason"],"STOP_OR_GAP")
        self.assertEqual(closed["shares_sold"],5)
        self.assertFalse(closed["live_trade_authorized"])
        with self.assertRaisesRegex(ValueError,"NOT_ARMED_OR_OPEN"):
            restarted.observe(pid,120,t+5,now=t+5,verified_feed=True)

    def test_partial_target_then_breakeven_and_trailing_stop(self):
        pid=self.row["id"];t=self.params["now"]
        self.book.decide(pid,self.row["fingerprint"],"yes",5,now=t+1)
        self.book.observe(pid,100.5,t+2,now=t+2,verified_feed=True,
                          session_day="2026-10-16")
        part=self.book.observe(pid,112.5,t+3,now=t+3,verified_feed=True)
        self.assertEqual(part["state"],"OPEN_PAPER_ONLY")
        self.assertEqual(part["shares_remaining"],3)
        self.assertEqual(part["shares_sold"],2)
        self.assertEqual(part["stop_current"],100.5)
        later=self.book.observe(pid,109,t+4,now=t+4,verified_feed=True,
                                session_day="2026-10-19",session_closed=True,
                                atr=3.)
        self.assertEqual(later["stop_current"],103.)
        filled=self.book.observe(pid,102,t+5,now=t+5,verified_feed=True)
        self.assertEqual(filled["exit_reason"],"STOP_OR_GAP")
        self.assertEqual(filled["shares_sold"],5)
        self.assertEqual(filled["shares_remaining"],0)

    def test_target2_and_single_share_target1_are_terminal(self):
        t=self.params["now"];pid=self.row["id"]
        self.book.decide(pid,self.row["fingerprint"],"yes",1,now=t+1)
        self.book.observe(pid,101,t+2,now=t+2,verified_feed=True)
        first=self.book.observe(pid,112,t+3,now=t+3,verified_feed=True)
        self.assertEqual(first["exit_reason"],"TARGET_2R_SINGLE_SHARE")
        self.assertEqual(first["shares_sold"],1)
        other=self.book.propose(**dict(
            self.params,symbol="SYNTHTWO",source_sha="b"*64
        ))
        self.book.decide(other["id"],other["fingerprint"],"yes",2,now=t+1)
        self.book.observe(other["id"],100.5,t+2,now=t+2,verified_feed=True)
        second=self.book.observe(
            other["id"],119,t+3,now=t+3,verified_feed=True)
        self.assertEqual(second["exit_reason"],"TARGET_3R")
        self.assertEqual(second["shares_sold"],2)

    def test_malformed_or_duplicate_quote_blocked(self):
        t=self.params["now"];pid=self.row["id"]
        self.book.decide(pid,self.row["fingerprint"],"yes",1,now=t+1)
        with self.assertRaisesRegex(ValueError,"FRESH_SOURCE_VERIFICATION_REQUIRED"):
            self.book.observe(pid,100,t+2,now=t+2)
        with self.assertRaisesRegex(ValueError,"STALE_OR_FUTURE_QUOTE"):
            self.book.observe(pid,100,t+2,now=t+50,verified_feed=True)
        with self.assertRaisesRegex(ValueError,"QUOTE_BEFORE_APPROVAL"):
            self.book.observe(pid,100,t,now=t,verified_feed=True)
        self.book.observe(pid,100,t+2,now=t+2,verified_feed=True)
        with self.assertRaisesRegex(ValueError,"DUPLICATE_OR_REVERSED_FEED_TICK"):
            self.book.observe(pid,103,t+2,now=t+3,verified_feed=True)

    def test_source_plan_identity_hash_and_risk_cap_never_reset(self):
        original=self.row
        duplicate=self.book.propose(**self.params)
        self.assertEqual(duplicate["id"],original["id"])
        with self.assertRaisesRegex(ValueError,"SIGNAL_HAS_CONFLICTING_PRICE_PLAN"):
            self.book.propose(**dict(self.params,stop=94))
        self.book.decide(original["id"],original["fingerprint"],
                         "no",now=self.params["now"]+1)
        self.assertEqual(self.book.propose(**self.params)["state"],"REJECTED")
        with self.book.db() as conn:
            conn.execute(
                "UPDATE trades SET plan='{}' WHERE id=?",(original["id"],)
            )
        with self.assertRaisesRegex(ValueError,"TRADE_PLAN_TAMPERED"):
            self.book.get(original["id"])

    def test_strict_quantity_and_invalid_price_plan_guard(self):
        for value in (0,-1,True,1.2,"1.0","3e2","0","30000000"):
            proposal=self.book.propose(**dict(
                self.params,symbol="QTY"+str(len(str(value))),
                source_sha=("b" if value is True else "c")*64
            ))
            with self.assertRaises(ValueError):
                self.book.decide(
                    proposal["id"],proposal["fingerprint"],"yes",value,
                    now=self.params["now"]+1,
                )
        with self.assertRaises(ValueError):
            self.book.propose(**dict(self.params,stop=101))
        with self.assertRaises(ValueError):
            self.book.propose(**dict(self.params,capital_inr=1))

    def test_stale_paper_position_not_treated_as_live_protection(self):
        row=self.book.get(self.row["id"])
        self.assertEqual(row["broker_preference"],"GROWW")
        self.assertTrue(row["paper_only"])
        self.assertFalse(row["plan"]["strategy_profitable_proven"])
        self.assertFalse(row["plan"]["quote_source_independently_verified"])
        self.assertEqual(row["plan"]["max_holding_sessions"],100)


if __name__=="__main__":
    unittest.main()
