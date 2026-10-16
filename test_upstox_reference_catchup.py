"""Synthetic capped manual catchup tests, zero API/network/transactions."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from upstox_reference_catchup import (
    SOURCE, catch_up,
)
from upstox_reference_batches import ReferenceBatchError, _stored_paths

TOKEN="SYNTHETIC_MANUAL_REFERENCE_ONLY_TOKEN"


class SyntheticCollector:
    def __init__(self, *, initial=80, remaining=1190,
                 fatal_on=None, all_fail=False, inconsistent=False):
        self.initial=initial
        self.remaining=remaining
        self.fatal_on=fatal_on
        self.all_fail=all_fail
        self.inconsistent=inconsistent
        self.calls=[]
        self.working=initial

    def __call__(self,root,token,batch):
        self.calls.append(batch)
        if self.fatal_on==len(self.calls):
            attempted=1
            completed=0
            requests=1
            stop="UPSTOX_HTTP_429"
        else:
            attempted=min(batch,self.remaining)
            completed=0 if self.all_fail else attempted
            requests=4*attempted
            stop=None
            self.remaining-=completed
            self.working+=completed
        return {
            "scope":"NSE_EQ_UPSTOX_SECONDARY_VENDOR_INCREMENTAL_REFERENCE_ONLY",
            "batch_attempted_companies":attempted,
            "new_company_records":completed,
            "requests_this_run":requests,
            "stop_reason":stop,
            "mapping_symbols":1270,
            "completed_company_records":self.working,
            "remaining_uncollected":self.remaining,
            "live_orders":"PAPER_ORDERS_ENABLED" if self.inconsistent
                          else "DISABLED",
            "strict_fundamental_pass_changes":0,
        }


class CatchupTests(unittest.TestCase):
    def test_four_serial_25_company_batches_and_no_orders(self):
        with tempfile.TemporaryDirectory() as d:
            collector=SyntheticCollector()
            rests=[]
            result=catch_up(
                d,TOKEN,max_attempts=100,collector=collector,
                rest=lambda secs:rests.append(secs)
            )
            self.assertEqual(result["scope"],SOURCE)
            self.assertEqual(collector.calls,[25,25,25,25])
            self.assertEqual(result["companies_attempted"],100)
            self.assertEqual(result["new_company_records"],100)
            self.assertEqual(result["requests_this_manual_session"],400)
            self.assertEqual(result["completed_batches"],4)
            self.assertEqual(result["completed_company_records"],180)
            self.assertEqual(result["remaining_uncollected"],1090)
            self.assertEqual(result["auto_daily_limit_unchanged"],25)
            self.assertEqual(result["official_nse_verified_years_added"],0)
            self.assertEqual(result["strict_fundamental_pass_changes"],0)
            self.assertEqual(result["orders"],"DISABLED")
            self.assertEqual(rests,[10,10,10])
            self.assertNotIn(TOKEN,json.dumps(result))
            path=_stored_paths(d)[0]/"last_manual_catchup.json"
            self.assertTrue(path.exists())
            self.assertNotIn(TOKEN,path.read_text())

    def test_manual_catchup_respects_small_cap(self):
        with tempfile.TemporaryDirectory() as d:
            collector=SyntheticCollector()
            result=catch_up(d,TOKEN,max_attempts=28,collector=collector,
                            rest=lambda _delay:None)
            self.assertEqual(collector.calls,[25,3])
            self.assertEqual(result["new_company_records"],28)
            self.assertEqual(result["requests_this_manual_session"],112)

    def test_provider_429_halts_without_next_batch(self):
        with tempfile.TemporaryDirectory() as d:
            collector=SyntheticCollector(fatal_on=2)
            result=catch_up(d,TOKEN,max_attempts=100,collector=collector,
                            rest=lambda _delay:None)
            self.assertEqual(collector.calls,[25,25])
            self.assertEqual(result["state"],
                             "STOPPED_ON_PROVIDER_LIMIT_OR_NETWORK_CONSTRAINT")
            self.assertEqual(result["new_company_records"],25)
            self.assertEqual(result["completed_batches"],2)
            self.assertEqual(result["requests_this_manual_session"],101)
            self.assertEqual(result["orders"],"DISABLED")

    def test_zero_success_stops_before_provider_spam(self):
        with tempfile.TemporaryDirectory() as d:
            collector=SyntheticCollector(all_fail=True)
            result=catch_up(d,TOKEN,collector=collector,rest=lambda _x:None)
            self.assertEqual(collector.calls,[25])
            self.assertEqual(result["state"],
                             "STOPPED_ON_ZERO_SUCCESS_SOURCE_REVIEW_REQUIRED")
            self.assertEqual(result["new_company_records"],0)
            self.assertEqual(result["strict_fundamental_pass_changes"],0)

    def test_mapping_provenance_exception_stops_without_leak(self):
        class Failed:
            def __init__(self):
                self.calls=0
            def __call__(self,*args):
                self.calls+=1
                raise ReferenceBatchError("UPSTOX_MAP_PROVENANCE_UNTRUSTED")
        with tempfile.TemporaryDirectory() as d:
            collector=Failed()
            result=catch_up(d,TOKEN,collector=collector,
                            rest=lambda _delay:None)
            self.assertEqual(collector.calls,1)
            self.assertEqual(result["state"],
                             "STOPPED_ON_REFERENCE_OR_MAPPING_GUARD")
            self.assertEqual(result["requests_this_manual_session"],0)
            self.assertNotIn(TOKEN,str(result))

    def test_untrusted_collector_order_enablement_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            collector=SyntheticCollector(inconsistent=True)
            result=catch_up(d,TOKEN,collector=collector,
                            rest=lambda _delay:None)
            self.assertEqual(collector.calls,[25])
            self.assertEqual(result["state"],"STOPPED_ON_UNTRUSTED_BATCH_RESULT")
            self.assertEqual(result["new_company_records"],0)

    def test_hard_stop_100_and_missing_token(self):
        with tempfile.TemporaryDirectory() as d:
            collector=SyntheticCollector()
            for amount in (0,101,True,False,-1,1.5):
                with self.assertRaises(ValueError):
                    catch_up(d,TOKEN,max_attempts=amount,collector=collector)
            with self.assertRaises(ReferenceBatchError):
                catch_up(d,"",collector=collector)
            self.assertEqual(collector.calls,[])


if __name__=="__main__":
    unittest.main()
