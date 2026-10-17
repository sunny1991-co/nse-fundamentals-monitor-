"""Shared research capital tests; broker execution stays impossible."""
import tempfile
import threading
import unittest
from pathlib import Path

from research_monitor.assistant_trade_queue import ApprovalQueue


class SharedPaperBudgetTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/"paper"/"assistant.sqlite3"
        self.book=ApprovalQueue(self.path)
        self.t=1792110000
        self.base=dict(
            signal_day="2026-10-16",entry_min=100,entry_max=101,
            stop=95,target_1=112,target_2=118,capital_inr=100_000,
            signal_price=100.5,now=self.t,ttl=300,
        )

    def plan(self,num,*,stop=95,capital=100_000,symbol=None,signal_day=None):
        params=dict(self.base,
            symbol=symbol or f"SYNTH{num}",
            stop=stop,capital_inr=capital,
            source_sha=f"{num:064x}",
        )
        if signal_day is not None:
            params["signal_day"]=signal_day
        return self.book.propose(**params)

    def approve(self,row,qty):
        return self.book.decide(
            row["id"],row["fingerprint"],"yes",qty,now=self.t+1
        )

    def test_three_risk_reservations_pass_fourth_is_blocked(self):
        rows=[self.plan(i) for i in range(1,5)]
        qty=rows[0]["plan"]["maximum_approved_quantity"]
        self.assertEqual(qty,151)
        for row in rows[:3]:
            self.assertEqual(self.approve(row,qty)["state"],"ARMED_PAPER_ONLY")
        with self.assertRaisesRegex(ValueError,"SHARED_PAPER_STOP_RISK_LIMIT"):
            self.approve(rows[3],qty)
        self.assertEqual(self.book.get(rows[3]["id"])["state"],"WAITING_FOR_OWNER")

    def test_four_notional_reservations_pass_fifth_is_blocked(self):
        rows=[self.plan(i,stop=99.5) for i in range(1,6)]
        qty=rows[0]["plan"]["maximum_approved_quantity"]
        self.assertEqual(qty,246)
        for row in rows[:4]:
            self.approve(row,qty)
        with self.assertRaisesRegex(ValueError,"SHARED_PAPER_NOTIONAL_LIMIT"):
            self.approve(rows[4],qty)

    def test_no_duplicate_symbol_even_with_new_signal_or_source(self):
        one=self.plan(1)
        self.approve(one,1)
        other=self.plan(2,symbol="SYNTH1",signal_day="2026-10-15")
        with self.assertRaisesRegex(ValueError,"SYMBOL_ALREADY_ARMED_OR_OPEN_PAPER"):
            self.approve(other,1)
        self.assertEqual(self.book.get(other["id"])["state"],"WAITING_FOR_OWNER")

    def test_different_fictional_capital_cannot_stack_reservations(self):
        self.approve(self.plan(1),1)
        other=self.plan(2,capital=200_000)
        with self.assertRaisesRegex(ValueError,"SHARED_PAPER_CAPITAL_MISMATCH"):
            self.approve(other,1)

    def test_closed_paper_trade_releases_budget_after_restart(self):
        old=self.plan(1)
        qty=old["plan"]["maximum_approved_quantity"]
        self.approve(old,qty)
        self.book.observe(old["id"],100.5,self.t+2,now=self.t+2,
                          verified_feed=True,session_day="2026-10-16")
        finished=self.book.observe(old["id"],90,self.t+3,now=self.t+3,
                                   verified_feed=True)
        self.assertEqual(finished["state"],"CLOSED_PAPER_ONLY")
        restarted=ApprovalQueue(self.path)
        others=[self.plan(i) for i in range(2,5)]
        for row in others:
            self.assertEqual(
                restarted.decide(row["id"],row["fingerprint"],"yes",qty,
                                 now=self.t+4)["state"],
                "ARMED_PAPER_ONLY"
            )

    def test_simultaneous_approvals_cannot_overbook_budget(self):
        rows=[self.plan(i) for i in range(1,5)]
        qty=rows[0]["plan"]["maximum_approved_quantity"]
        barrier=threading.Barrier(4)
        results=[]
        lock=threading.Lock()

        def worker(row):
            barrier.wait()
            try:
                state=ApprovalQueue(self.path).decide(
                    row["id"],row["fingerprint"],"yes",qty,now=self.t+1
                )["state"]
            except ValueError as exc:
                state=str(exc)
            with lock:
                results.append(state)

        threads=[threading.Thread(target=worker,args=(row,)) for row in rows]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(results.count("ARMED_PAPER_ONLY"),3)
        self.assertEqual(results.count("SHARED_PAPER_STOP_RISK_LIMIT"),1)


if __name__=="__main__":
    unittest.main()
