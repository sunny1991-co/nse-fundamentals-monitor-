"""Synthetic no-network resumable read-only vendor reference batch tests."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import upstox_reference_batches as batch
from test_upstox_reference_pilot import FakeSession, TOKEN


def mapping(n=6):
    return {
        "RKFORGE": "INE399G01023",
        "INDIANB": "INE562A01011",
        "DEEDEV": "INE841L01016",
        "PALREDTEC": "INE218G01033",
        "HCG": "INE075I01017",
        "RELIANCE": "INE002A01018",
    }


def loader_from(data):
    return lambda _root: (dict(data),"a"*64)


class BoundedBatchTests(unittest.TestCase):
    def test_bounded_3_then_resume_skips_cached_symbols(self):
        with tempfile.TemporaryDirectory() as d:
            mp=mapping()
            a=batch.collect(d,TOKEN,3,session=FakeSession(),
                            pause=lambda _:None,mapping_loader=loader_from(mp),
                            seed_existing=False)
            self.assertEqual(a["new_company_records"],3)
            self.assertEqual(a["completed_company_records"],3)
            self.assertEqual(a["remaining_uncollected"],3)
            self.assertEqual(a["requests_this_run"],12)
            self.assertEqual(a["strict_fundamental_pass_changes"],0)
            self.assertFalse(a["strategy_trading_enabled"])
            self.assertEqual(a["live_orders"],"DISABLED")
            self.assertNotIn(TOKEN,str(a))

            second_session=FakeSession()
            b=batch.collect(d,TOKEN,3,session=second_session,
                            pause=lambda _:None,mapping_loader=loader_from(mp),
                            seed_existing=False)
            self.assertEqual(b["previous_company_records"],3)
            self.assertEqual(b["new_company_records"],3)
            self.assertEqual(b["completed_company_records"],6)
            self.assertEqual(b["remaining_uncollected"],0)
            self.assertEqual(len(second_session.calls),12)
            third_session=FakeSession()
            c=batch.collect(d,TOKEN,3,session=third_session,
                            pause=lambda _:None,mapping_loader=loader_from(mp),
                            seed_existing=False)
            self.assertEqual(c["new_company_records"],0)
            self.assertEqual(c["requests_this_run"],0)
            self.assertEqual(third_session.calls,[])
            stored=list(Path(d).rglob("records/*.json"))
            self.assertEqual(len(stored),6)
            for path in stored:
                row=json.loads(path.read_text())
                self.assertFalse(row["is_nse_audited_verified"])
                self.assertFalse(row["nse_sector_verified"])
                self.assertFalse(row["strict_screen_approved"])
                self.assertFalse(row["trade_eligible"])

    def test_http_rate_limit_stops_before_next_symbol(self):
        with tempfile.TemporaryDirectory() as d:
            source=FakeSession(fail=True)
            result=batch.collect(d,TOKEN,5,session=source,
                                 pause=lambda _:None,
                                 mapping_loader=loader_from(mapping()),
                                 seed_existing=False)
            self.assertEqual(len(source.calls),1)
            self.assertEqual(result["new_company_records"],0)
            self.assertEqual(result["completed_company_records"],0)
            self.assertEqual(result["stop_reason"],"UPSTOX_HTTP_429")
            self.assertEqual(result["requests_this_run"],1)
            self.assertFalse(list(Path(d).rglob("records/*.json")))

    def test_undocumented_fundamental_schema_never_checkpointed(self):
        class BadSession(FakeSession):
            def get(self,url,**kwargs):
                response=super().get(url,**kwargs)
                if url.endswith("/income-statement"):
                    response.data["type"]="standalone"
                return response
        with tempfile.TemporaryDirectory() as d:
            session=BadSession()
            result=batch.collect(d,TOKEN,1,session=session,
                                 pause=lambda _:None,
                                 mapping_loader=loader_from(mapping()),
                                 seed_existing=False)
            self.assertEqual(result["new_company_records"],0)
            self.assertEqual(result["completed_company_records"],0)
            self.assertEqual(
                result["batch_results"][0]["status"],
                "UPSTOX_CONSOLIDATED_YEARLY_UNITS_NOT_VERIFIED"
            )
            self.assertFalse(list(Path(d).rglob("records/*.json")))

    def test_soft_vendor_404_waits_24h_and_next_issuer_can_advance(self):
        class FirstSymbol404(FakeSession):
            def __init__(self):
                super().__init__()
                self.fail_for_first=True
            def get(self,url,**kwargs):
                if self.fail_for_first:
                    self.fail_for_first=False
                    response=super().get(url,**kwargs)
                    response.status_code=404
                    return response
                return super().get(url,**kwargs)

        with tempfile.TemporaryDirectory() as d:
            items=mapping()
            first_session=FirstSymbol404()
            first=batch.collect(
                d,TOKEN,1,session=first_session,
                pause=lambda _:None,mapping_loader=loader_from(items),
                seed_existing=False
            )
            self.assertEqual(first["cached_soft_failures"],1)
            self.assertEqual(first["new_company_records"],0)
            self.assertEqual(first["requests_this_run"],1)
            second_session=FakeSession()
            second=batch.collect(
                d,TOKEN,1,session=second_session,
                pause=lambda _:None,mapping_loader=loader_from(items),
                seed_existing=False
            )
            self.assertEqual(second["new_company_records"],1)
            self.assertEqual(second["soft_failure_companies_skipped_during_cooldown"],1)
            self.assertEqual(len(second_session.calls),4)
            self.assertNotEqual(
                second["batch_results"][0]["symbol"],
                first["batch_results"][0]["symbol"]
            )

            folder=Path(d)/"runtime"/"experiments"/batch.FOLDER/batch.VERSION
            failed_path=folder/"soft_failures.json"
            ledger=json.loads(failed_path.read_text())
            from datetime import datetime, timezone, timedelta
            failing=first["batch_results"][0]["symbol"]
            ledger["issues"][failing]["at_utc"]=(
                datetime.now(timezone.utc)-timedelta(hours=25)
            ).isoformat()
            failed_path.write_text(json.dumps(ledger))
            third=batch.collect(
                d,TOKEN,25,session=FakeSession(),
                pause=lambda _:None,mapping_loader=loader_from(items),
                seed_existing=False
            )
            self.assertEqual(third["cached_soft_failures"],0)
            self.assertEqual(third["remaining_uncollected"],0)
            self.assertEqual(third["completed_company_records"],len(items))


    def test_pilot_can_seed_only_same_matching_isin(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            folder=root/"runtime"/"experiments"/batch.FOLDER
            folder.mkdir(parents=True)
            old_result={
                "scope":"NSE_MAINBOARD_UPSTOX_SECONDARY_VENDOR_REFERENCE_ONLY",
                "results":[
                    {"symbol":"RELIANCE","isin":"INE002A01018",
                     "status":"UPSTOX_REFERENCE_ONLY_NOT_NSE_VERIFIED",
                     "financials":{"Mar 2023":{}},
                     "is_nse_audited_verified":False,
                     "nse_sector_verified":False,
                     "vendor_sector_reference":"Refineries"},
                    {"symbol":"INDIANB","isin":"INE_NOT_MATCHED",
                     "status":"UPSTOX_REFERENCE_ONLY_NOT_NSE_VERIFIED",
                     "financials":{"Mar 2023":{}},
                     "is_nse_audited_verified":False,
                     "nse_sector_verified":False},
                ],
            }
            (folder/"report.json").write_text(json.dumps(old_result))
            session=FakeSession()
            a=batch.collect(root,TOKEN,1,session=session,pause=lambda _:None,
                            mapping_loader=loader_from(mapping()))
            self.assertEqual(a["restored_from_previously_verified_vendor_pilot"],1)
            self.assertEqual(a["new_company_records"],1)
            self.assertEqual(a["completed_company_records"],2)
            self.assertEqual(len(session.calls),4)
            saved=json.loads((
                folder/batch.VERSION/"records"/"RELIANCE.json"
            ).read_text())
            self.assertFalse(saved["strict_screen_approved"])

    def test_existing_record_isin_changed_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            first=batch.collect(d,TOKEN,1,session=FakeSession(),
                                pause=lambda _:None,
                                mapping_loader=loader_from(mapping()),
                                seed_existing=False)
            stored=list(Path(d).rglob("records/*.json"))
            self.assertEqual(len(stored),1)
            sym=stored[0].stem
            changed=mapping()
            changed[sym]="INE999999999"
            with self.assertRaisesRegex(
                batch.ReferenceBatchError,
                "REFERENCE_RECORD_REQUIRES_MANUAL_AUDIT"
            ):
                batch.collect(d,TOKEN,1,session=FakeSession(),
                              pause=lambda _:None,
                              mapping_loader=loader_from(changed),
                              seed_existing=False)

    def test_absent_auth_and_invalid_batch_never_connect(self):
        with tempfile.TemporaryDirectory() as d:
            s=FakeSession()
            for limit in (0,26,True,-1,5.5):
                with self.assertRaises(ValueError):
                    batch.collect(d,TOKEN,limit,session=s,
                                  mapping_loader=loader_from(mapping()))
            with self.assertRaisesRegex(batch.ReferenceBatchError,"TOKEN_MISSING"):
                batch.collect(d,"",5,session=s,
                              mapping_loader=loader_from(mapping()))
            self.assertEqual(s.calls,[])

    def test_missing_mapping_metadata_fails_before_network(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(batch.ReferenceBatchError):
                batch.verified_reference_map(d)
            with self.assertRaises(batch.ReferenceBatchError):
                batch.collect(d,TOKEN,5,session=FakeSession(),
                              seed_existing=False)

    def test_parallel_manual_and_auto_batch_cannot_both_write(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/"runtime"/"experiments"/batch.FOLDER/batch.VERSION
            folder.mkdir(parents=True)
            path=folder/"collection.lock"
            session=FakeSession()
            with path.open("a+") as other:
                fcntl.flock(other.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(
                    batch.ReferenceBatchError,"VENDOR_BATCH_ALREADY_RUNNING"
                ):
                    batch.collect(
                        d,TOKEN,1,session=session,pause=lambda _:None,
                        mapping_loader=loader_from(mapping()),
                        seed_existing=False,
                    )
                fcntl.flock(other.fileno(),fcntl.LOCK_UN)
            self.assertEqual(session.calls,[])
            worked=batch.collect(
                d,TOKEN,1,session=FakeSession(),pause=lambda _:None,
                mapping_loader=loader_from(mapping()),seed_existing=False,
            )
            self.assertEqual(worked["new_company_records"],1)


    def test_reference_map_timestamp_and_provenance_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            folder=root/"runtime"/"experiments"/batch.FOLDER
            folder.mkdir(parents=True)
            (root/"FY2425.csv").write_text("Symbol\nRELIANCE\n")
            (root/"NSE_FY2025-26_Consolidated_Fundamentals.csv").write_text(
                "Symbol\nRELIANCE\n"
            )
            metadata={
                "scope":"CURRENT_NSE_EQ_VENDOR_CROSSWALK_ONLY_NOT_MAINBOARD_ATTESTATION",
                "source_url":batch.MASTER_URL,
                "source_sha256":"a"*64,
                "data_source":"UPSTOX_PUBLIC_NSE_BOD_JSON_GZIP",
                "verified_mainboard_history":False,
                "nse_xbrl_verified":False,
                "fetched_at_utc":"2020-01-01T00:00:00+00:00",
                "mapping_path":str(folder/batch.MAP_NAME),
                "verified_by_vendor_crosswalk":1,
            }
            (folder/batch.METADATA_NAME).write_text(json.dumps(metadata))
            with self.assertRaisesRegex(batch.ReferenceBatchError,
                                        "UPSTOX_MAP_STALE"):
                batch.verified_reference_map(root)
            metadata["fetched_at_utc"]="2026-10-16T12:00:00+00:00"
            (folder/batch.METADATA_NAME).write_text(json.dumps(metadata))
            (folder/batch.MAP_NAME).write_text(
                "SYMBOL,ISIN,SERIES,SOURCE\n"
                "RELIANCE,INE002A01018,EQ,UPSTOX_NSE_BOD_CURRENT_VENDOR_MAP\n"
            )
            from datetime import datetime,timezone
            pairs,digest=batch.verified_reference_map(
                root,datetime(2026,10,16,14,0,tzinfo=timezone.utc)
            )
            self.assertEqual(pairs,{"RELIANCE":"INE002A01018"})
            self.assertEqual(len(digest),64)
            metadata["source_url"]="https://wrong.example.com/master"
            (folder/batch.METADATA_NAME).write_text(json.dumps(metadata))
            with self.assertRaisesRegex(batch.ReferenceBatchError,
                                        "UPSTOX_MAP_PROVENANCE_UNTRUSTED"):
                batch.verified_reference_map(
                    root,datetime(2026,10,16,14,0,tzinfo=timezone.utc)
                )


if __name__=="__main__":
    unittest.main()
