"""Synthetic NIFTY PR analysis only: protects TRI and broker boundaries."""
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from nifty_price_return_import import SUPPORTED, TYPE, csv_bytes
from nifty_pr_market_context import compare, read_index


def weekdays(n=320):
    d = date(2022, 1, 3)
    answer = []
    while len(answer) < n:
        if d.weekday() < 5:
            answer.append(d)
        d += timedelta(days=1)
    return answer


class NiftyPriceContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dates = weekdays(340)

    def write(self, name, *, omit=(), multiplier='1', change_after=None):
        dates = [d for d in self.dates if d not in omit]
        values = {}
        for n, day in enumerate(dates):
            index_number = self.dates.index(day)
            base = Decimal('100') + Decimal(index_number) * Decimal(multiplier)
            if change_after and day > change_after:
                base *= 2
            values[day] = (base, base+2, base-2, base+1)
        payload = csv_bytes(values, name)
        digest = hashlib.sha256(payload).hexdigest()
        basename = SUPPORTED[name]
        (self.root / (basename+'.csv')).write_bytes(payload)
        meta = {
            "type": TYPE,
            "return_kind": "PRICE_RETURN_NOT_TRI",
            "index_name": name,
            "operator_source_checked": False,
            "eligible_for_tri_strategy_benchmark": False,
            "live_orders": "DISABLED",
            "sha256": digest,
            "rows": len(dates),
            "first_date": min(dates).isoformat(),
            "last_date": max(dates).isoformat(),
        }
        (self.root / (basename+'_metadata.json')).write_text(json.dumps(meta))
        return values

    def make_pair(self):
        self.write("NIFTY 50", multiplier='.1')
        self.write("NIFTY 100", multiplier='.3')

    def test_dual_index_metrics_dont_unlock_tri_or_orders(self):
        self.make_pair()
        result = compare(self.root, today=date(2026,10,17))
        self.assertEqual(result["exact_common_session_count"], 340)
        self.assertEqual(result["as_of_last_exact_common_date"],
                         self.dates[-1].isoformat())
        self.assertEqual(result["market_context"]["NIFTY 50"]["historical_price_state"],
                         "POSITIVE_TREND")
        self.assertGreater(result["relative_index_price_returns"][
            "63_session_nifty100_minus_nifty50_pct_points"],0)
        self.assertEqual(result["benchmark_kind"],
                         "PRICE_RETURN_EX_DIVIDENDS_NOT_GROSS_TRI")
        self.assertFalse(result["tri_required_strategies_unblocked"])
        self.assertFalse(result["stock_strategy_backtest_conducted"])
        self.assertFalse(result["real_profitability_verified"])
        self.assertEqual(result["orders"],"DISABLED")
        self.assertFalse((self.root/"nifty50_tri_history.csv").exists())

    def test_tampered_csv_rejected_without_receipt_update(self):
        self.make_pair()
        path=self.root/"nifty50_pr_history.csv"
        path.write_bytes(path.read_bytes().replace(b'NIFTY 50',b'NIFTY 20'))
        with self.assertRaisesRegex(ValueError, "PR_CONTEXT_SOURCE_INTEGRITY_INVALID"):
            read_index(self.root,"NIFTY 50")

    def test_forged_tri_metadata_rejected_even_with_valid_hash(self):
        self.make_pair()
        meta_path = self.root/"nifty50_pr_history_metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["return_kind"] = "TOTAL_RETURN"
        meta["eligible_for_tri_strategy_benchmark"] = True
        meta_path.write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "PR_CONTEXT_SOURCE_INTEGRITY_INVALID"):
            compare(self.root,today=date(2026,10,17))

    def test_exact_matching_dates_no_forward_fill(self):
        miss=self.dates[-30]
        self.write("NIFTY 50", omit={miss}, multiplier='.1')
        self.write("NIFTY 100", multiplier='.3')
        result=compare(self.root,today=date(2026,10,17))
        self.assertEqual(result["exact_common_session_count"],339)
        self.assertEqual(result["unmatched_session_dates"],
                         {"NIFTY 50 only":0,"NIFTY 100 only":1})

    def test_future_rows_do_not_change_old_cutoff_metrics(self):
        cut=self.dates[-20]
        self.make_pair()
        before=compare(self.root,as_of=cut,today=date(2026,10,17))
        self.write("NIFTY 50", multiplier='.1', change_after=cut)
        self.write("NIFTY 100", multiplier='.3', change_after=cut)
        after=compare(self.root,as_of=cut,today=date(2026,10,17))
        self.assertEqual(before["market_context"],after["market_context"])
        self.assertEqual(before["relative_index_price_returns"],
                         after["relative_index_price_returns"])
        self.assertEqual(before["as_of_last_exact_common_date"],
                         after["as_of_last_exact_common_date"])
        self.assertNotEqual(before["inputs_sha256"], after["inputs_sha256"])

    def test_invalid_future_asof_rejected(self):
        self.make_pair()
        with self.assertRaisesRegex(ValueError,"PR_CONTEXT_AS_OF_INVALID"):
            compare(self.root,as_of=date(2030,1,1),today=date(2026,10,17))

    def test_insufficient_shared_sessions_rejected(self):
        self.make_pair()
        cut=self.dates[200]
        with self.assertRaisesRegex(ValueError,"PR_CONTEXT_253_COMMON_SESSIONS_REQUIRED"):
            compare(self.root,as_of=cut,today=date(2026,10,17))

    def test_many_misaligned_dates_rejected(self):
        missing=set(self.dates[-100:-50])
        self.write("NIFTY 50", omit=missing, multiplier='.1')
        self.write("NIFTY 100", multiplier='.3')
        with self.assertRaisesRegex(ValueError,"PR_CONTEXT_TOO_MANY_UNMATCHED_DATES"):
            compare(self.root,today=date(2026,10,17))


if __name__ == "__main__":
    unittest.main()
