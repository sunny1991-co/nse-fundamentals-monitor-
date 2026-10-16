"""Synthetic restart/schedule security tests: no real API traffic or tokens."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

import termux_upstox_reference_worker as worker
from upstox_reference_batches import ReferenceBatchError
from research_monitor.upstox_instrument_master import InstrumentMapUnavailable


TOKEN="DO_NOT_DISCLOSE_SYNTHETIC_LOCAL_TOKEN_2026"


def local(day="2026-10-16",clock="20:15"):
    return datetime.fromisoformat(f"{day}T{clock}:00+05:30")


class Stub:
    def __init__(self, result=None, error=None):
        self.count=0
        self.calls=[]
        self.result=result if result is not None else {
            "completed_company_records":30,
            "remaining_uncollected":1240,
            "mapping_symbols":1270,
            "requests_this_run":100,
            "new_company_records":25,
            "stop_reason":None,
        }
        self.error=error
    def __call__(self, root, token, batch):
        self.count+=1
        self.calls.append((root,token,batch))
        if self.error:
            raise self.error
        return dict(self.result)


class Refresh:
    def __init__(self,error=None):
        self.count=0
        self.error=error
    def __call__(self, root, now):
        self.count+=1
        if self.error:
            raise self.error


class DailyVendorTests(unittest.TestCase):
    def test_after_market_run_stores_only_research_and_one_daily_claim(self):
        with tempfile.TemporaryDirectory() as d:
            stub=Stub()
            refresh=Refresh()
            now=local()
            result=worker.run_daily(
                d, now, token=TOKEN, collector=stub,
                refresh_mapping=refresh,
            )
            self.assertEqual(result["state"],"COMPLETED_DAILY_REFERENCE_BATCH")
            self.assertEqual(result["new_company_records"],25)
            self.assertEqual(result["completed_company_records"],30)
            self.assertEqual(result["remaining_uncollected"],1240)
            self.assertEqual(stub.count,1)
            self.assertEqual(refresh.count,1)
            self.assertEqual(stub.calls[0][2],25)
            self.assertEqual(result["orders"],"DISABLED")
            self.assertFalse(result["trading_strategy_approved"])
            self.assertNotIn(TOKEN,json.dumps(result))
            stored=(Path(d)/"runtime"/worker.STATUS_NAME).read_text()
            self.assertNotIn(TOKEN,stored)
            again=worker.run_daily(
                d, now+timedelta(minutes=30), token=TOKEN,
                collector=stub, refresh_mapping=refresh,
            )
            self.assertEqual(again["state"],"ALREADY_ATTEMPTED_TODAY")
            self.assertEqual(stub.count,1)
            self.assertEqual(refresh.count,1)

    def test_next_ist_day_resumes_after_success(self):
        with tempfile.TemporaryDirectory() as d:
            stub=Stub()
            for day in ("2026-10-16","2026-10-17"):
                result=worker.run_daily(
                    d, local(day=day), token=TOKEN,collector=stub,
                    refresh_mapping=Refresh(),
                )
                self.assertEqual(result["state"],"COMPLETED_DAILY_REFERENCE_BATCH")
            self.assertEqual(stub.count,2)

    def test_market_hours_before_1700_do_not_claim_or_call(self):
        with tempfile.TemporaryDirectory() as d:
            stub=Stub()
            refresh=Refresh()
            for clock in ("00:05","09:20","15:31","16:59","23:55"):
                result=worker.run_daily(
                    d, local(clock=clock), token=TOKEN,
                    collector=stub,refresh_mapping=refresh
                )
                self.assertEqual(result["state"],"WAITING_POST_MARKET_IST")
            self.assertEqual(stub.count,0)
            self.assertEqual(refresh.count,0)
            self.assertFalse((Path(d)/"runtime"/worker.JOBS_NAME).exists())

    def test_absent_token_does_not_reserve_daily_slot(self):
        with tempfile.TemporaryDirectory() as d:
            stub=Stub()
            refresh=Refresh()
            a=worker.run_daily(
                d,local(),token="",collector=stub,refresh_mapping=refresh
            )
            self.assertEqual(a["state"],"WAITING_UPSTOX_ANALYTICS_TOKEN")
            self.assertFalse((Path(d)/"runtime"/worker.JOBS_NAME).exists())
            b=worker.run_daily(
                d,local(),token=TOKEN,collector=stub,refresh_mapping=refresh
            )
            self.assertEqual(b["state"],"COMPLETED_DAILY_REFERENCE_BATCH")
            self.assertEqual(stub.count,1)

    def test_crash_after_claim_does_not_repeat_in_same_day(self):
        with tempfile.TemporaryDirectory() as d:
            fail=Stub(error=ReferenceBatchError("UPSTOX_MAP_PROVENANCE_UNTRUSTED"))
            a=worker.run_daily(
                d,local(),token=TOKEN,collector=fail,refresh_mapping=Refresh()
            )
            self.assertEqual(a["state"],"STOPPED_ON_SOURCE_OR_MAPPING_GUARD")
            self.assertEqual(a["error_code"],"UPSTOX_MAP_PROVENANCE_UNTRUSTED")
            second=Stub()
            b=worker.run_daily(
                d,local(clock="20:55"),token=TOKEN,collector=second,
                refresh_mapping=Refresh()
            )
            self.assertEqual(b["state"],"ALREADY_ATTEMPTED_TODAY")
            self.assertEqual(second.count,0)
            next_day=worker.run_daily(
                d,local(day="2026-10-17"),token=TOKEN,collector=second,
                refresh_mapping=Refresh()
            )
            self.assertEqual(next_day["state"],"COMPLETED_DAILY_REFERENCE_BATCH")
            self.assertEqual(second.count,1)

    def test_http_429_stop_does_not_auto_retry_that_day(self):
        with tempfile.TemporaryDirectory() as d:
            source=Stub(result={
                "completed_company_records":5,
                "remaining_uncollected":1265,
                "mapping_symbols":1270,
                "requests_this_run":1,
                "new_company_records":0,
                "stop_reason":"UPSTOX_HTTP_429",
            })
            a=worker.run_daily(
                d,local(),token=TOKEN,collector=source,refresh_mapping=Refresh()
            )
            self.assertEqual(a["state"],"STOPPED_ON_VENDOR_API_CONSTRAINT")
            b=worker.run_daily(
                d,local(clock="21:00"),token=TOKEN,
                collector=source,refresh_mapping=Refresh(),
            )
            self.assertEqual(b["state"],"ALREADY_ATTEMPTED_TODAY")
            self.assertEqual(source.count,1)

    def test_mapping_sync_failure_does_not_start_company_api(self):
        with tempfile.TemporaryDirectory() as d:
            collected=Stub()
            failed=Refresh(error=InstrumentMapUnavailable(
                "UPSTOX_INSTRUMENT_MASTER_HTTP_403"
            ))
            a=worker.run_daily(
                d,local(),token=TOKEN,collector=collected,
                refresh_mapping=failed,
            )
            self.assertEqual(a["state"],"STOPPED_ON_SOURCE_OR_MAPPING_GUARD")
            self.assertEqual(a["error_code"],
                             "UPSTOX_INSTRUMENT_MASTER_HTTP_403")
            self.assertEqual(collected.count,0)
            self.assertEqual(failed.count,1)

    def test_inadvertent_secret_in_error_not_written(self):
        with tempfile.TemporaryDirectory() as d:
            fake=Stub(error=ReferenceBatchError("InvalidToken:"+TOKEN))
            report=worker.run_daily(
                d,local(),token=TOKEN,collector=fake,refresh_mapping=Refresh()
            )
            self.assertEqual(report["error_code"],"VENDOR_REFERENCE_ERROR")
            self.assertNotIn(TOKEN,report["error_code"])
            self.assertNotIn(TOKEN,(Path(d)/"runtime"/worker.STATUS_NAME).read_text())

    def test_batches_cannot_exceed_25_even_if_requested(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                worker.run_daily(d,local(),token=TOKEN,max_batch=26)
            with self.assertRaises(ValueError):
                worker.run_daily(d,local(),token=TOKEN,max_batch=True)
            self.assertFalse((Path(d)/"runtime"/worker.JOBS_NAME).exists())

    def test_fresh_mapping_no_refresh_needed_when_healthy(self):
        with tempfile.TemporaryDirectory() as d:
            # Evidence about the stale-map public refresh gate is separately
            # tested through a mocked map verifier and sync.
            from unittest.mock import patch
            with patch.object(worker,"verified_reference_map",
                              return_value=({"RELIANCE":"INE002A01018"},"z"*64)
                              ) as check, patch.object(
                worker,"sync_instruments"
            ) as sync:
                worker._refresh_only_expired_mapping(d,local().astimezone(timezone.utc))
            check.assert_called_once()
            sync.assert_not_called()

    def test_rejected_provenance_does_not_replace_map(self):
        with tempfile.TemporaryDirectory() as d:
            from unittest.mock import patch
            with patch.object(
                worker,"verified_reference_map",
                side_effect=ReferenceBatchError("UPSTOX_MAP_PROVENANCE_UNTRUSTED")
            ),patch.object(worker,"sync_instruments") as sync:
                with self.assertRaisesRegex(ReferenceBatchError,
                                            "PROVENANCE_UNTRUSTED"):
                    worker._refresh_only_expired_mapping(
                        d,local().astimezone(timezone.utc)
                    )
            sync.assert_not_called()

    def test_expired_only_map_triggers_one_public_refresh(self):
        with tempfile.TemporaryDirectory() as d:
            from unittest.mock import patch
            folder=worker._source_dir(d)
            folder.mkdir(parents=True)
            (folder/worker.METADATA_NAME).write_text(json.dumps({
                "fetched_at_utc":(local().astimezone(timezone.utc)
                                  -timedelta(days=8)).isoformat()
            }))
            def check(_root,now):
                check.calls+=1
                if check.calls == 1:
                    raise ReferenceBatchError("UPSTOX_MAP_STALE_OR_FUTURE_DATE")
                return {"RELIANCE":"INE002A01018"},"a"*64
            check.calls=0
            with patch.object(worker,"verified_reference_map",
                              side_effect=check),patch.object(
                                  worker,"sync_instruments") as sync:
                output=worker._refresh_only_expired_mapping(
                    d,local().astimezone(timezone.utc)
                )
            self.assertEqual(check.calls,2)
            sync.assert_called_once_with(Path(d))
            self.assertEqual(output[0]["RELIANCE"],"INE002A01018")

    def test_future_timestamp_fails_without_public_download(self):
        with tempfile.TemporaryDirectory() as d:
            from unittest.mock import patch
            folder=worker._source_dir(d)
            folder.mkdir(parents=True)
            (folder/worker.METADATA_NAME).write_text(json.dumps({
                "fetched_at_utc":(local().astimezone(timezone.utc)
                                  +timedelta(days=1)).isoformat()
            }))
            with patch.object(
                worker,"verified_reference_map",
                side_effect=ReferenceBatchError("UPSTOX_MAP_STALE_OR_FUTURE_DATE")
            ),patch.object(worker,"sync_instruments") as sync:
                with self.assertRaisesRegex(ReferenceBatchError,
                                            "CLOCK_OR_PROVENANCE_ERROR"):
                    worker._refresh_only_expired_mapping(
                        d,local().astimezone(timezone.utc)
                    )
            sync.assert_not_called()


if __name__=="__main__":
    unittest.main()
