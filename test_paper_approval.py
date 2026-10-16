import tempfile
import unittest
from pathlib import Path
from research_monitor.paper_approval import PaperApprovals


class PaperApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.sqlite3'
        self.book = PaperApprovals(self.path)
        self.args = dict(symbol='TEST', strategy='rehearsal-v1', broker='GROWW',
            capital=100000, entry_min=100, entry_max=101, stop=95, target=113,
            fee_fraction=.003, now=1000)
        self.row = self.book.create(**self.args)
        self.pid, self.digest = self.row['id'], self.row['digest']

    def approve(self):
        return self.book.decide(self.pid, self.digest, 'yes', now=1001)

    def test_no_or_unapproved_never_enters(self):
        with self.assertRaises(ValueError):
            self.book.observe(self.pid, 100, 1001, now=1001)
        self.book.decide(self.pid, self.digest, 'no', now=1001)
        with self.assertRaises(ValueError):
            self.book.observe(self.pid, 100, 1002, now=1002)

    def test_exact_plan_and_single_decision(self):
        with self.assertRaises(ValueError):
            self.book.decide(self.pid, 'wrong', 'yes', now=1001)
        self.approve()
        with self.assertRaises(ValueError):
            self.book.decide(self.pid, self.digest, 'yes', now=1002)

    def test_expired_approval_and_expired_entry(self):
        self.assertEqual(self.book.decide(self.pid, self.digest, 'yes', now=1300)['state'], 'EXPIRED')
        other = self.book.create(**self.args)
        self.book.decide(other['id'], other['digest'], 'yes', now=1001)
        self.assertEqual(self.book.observe(other['id'], 100, 1300, now=1300)['state'], 'EXPIRED')

    def test_restart_and_gap_stop(self):
        self.approve()
        self.book.observe(self.pid, 100, 1002, now=1002)
        restarted = PaperApprovals(self.path)
        closed = restarted.observe(self.pid, 85, 1400, now=1400)
        self.assertEqual(closed['state'], 'CLOSED_PAPER')
        self.assertEqual(closed['exit'], 85)
        self.assertEqual(closed['reason'], 'STOP')
        self.assertLess(closed['estimated_net_pnl_inr'], -closed['plan']['planned_risk_inr'])
        with self.assertRaises(ValueError):
            restarted.observe(self.pid, 114, 1401, now=1401)

    def test_no_chase_then_target(self):
        self.approve()
        self.assertEqual(self.book.observe(self.pid, 105, 1002, now=1002)['state'], 'APPROVED_PAPER')
        self.book.observe(self.pid, 101, 1003, now=1003)
        closed = self.book.observe(self.pid, 114, 1004, now=1004)
        self.assertEqual(closed['reason'], 'TARGET')
        self.assertGreater(closed['estimated_net_pnl_inr'], 0)

    def test_stale_future_duplicate_prices(self):
        self.approve()
        for price, stamp, now in [(100, 1001, 1020), (100, 1003, 1002), (float('nan'), 1002, 1002)]:
            with self.assertRaises(ValueError):
                self.book.observe(self.pid, price, stamp, now=now)
        self.book.observe(self.pid, 100, 1002, now=1002)
        with self.assertRaises(ValueError):
            self.book.observe(self.pid, 95, 1002, now=1003)

    def test_cost_aware_limits_and_bad_capital(self):
        p = self.row['plan']
        self.assertLessEqual(p['planned_risk_inr'], 1000)
        self.assertLessEqual(p['quantity'] * 101 * 1.003, 25000)
        self.assertFalse(p['live_eligible'])
        for bad in [0, -1, True, float('inf'), float('nan')]:
            with self.assertRaises(ValueError):
                self.book.create(**dict(self.args, capital=bad))

    def test_plan_tampering_detected(self):
        with self.book.db() as db:
            db.execute('UPDATE proposals SET plan=? WHERE id=?', ('{}', self.pid))
        with self.assertRaises(ValueError):
            self.approve()

    def test_price_before_approval_rejected(self):
        self.approve()
        with self.assertRaises(ValueError):
            self.book.observe(self.pid, 100, 1000, now=1002)

    def test_concurrent_yes_is_recorded_once(self):
        from concurrent.futures import ThreadPoolExecutor
        def approve_once(_):
            try:
                self.approve()
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sum(executor.map(approve_once, range(2))), 1)


if __name__ == '__main__':
    unittest.main()
