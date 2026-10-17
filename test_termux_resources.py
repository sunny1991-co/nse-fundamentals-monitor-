"""Android/Termux adaptive memory safety regressions (all synthetic data)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research_monitor.termux_resources import MIB, memory_snapshot, adaptive_command
from termux_research_worker import maintenance_due
import termux_maintenance


def meminfo(total: int, available: int) -> str:
    return ("MemTotal:       "+str(total * 1024)+" kB\n"
            "MemAvailable:   "+str(available * 1024)+" kB\n")


def profile(total: int, available: int):
    # Passing a larger cgroup cap avoids consulting host memory limits.
    return memory_snapshot(
        meminfo=meminfo(total,available),
        cgroup_limit=(total+1024)*MIB,
        cgroup_used=0,
    )


class TermuxRAMAdaptationTests(unittest.TestCase):
    def test_low_med_high_profile_from_available_not_free(self):
        for total,available,tier in [
            (2048, 1200, "LOW"),
            (6144, 3000, "MEDIUM"),
            (12288, 6000, "HIGH"),
        ]:
            with self.subTest(tier=tier):
                data=profile(total,available)
                self.assertEqual(data["tier"],tier)
                self.assertEqual(data["ram_total_mib"],total)
                self.assertEqual(data["ram_available_mib"],available)
                self.assertEqual(data["state"],"MEMORY_OK")
                self.assertTrue(data["heavy_research_allowed"])
                self.assertEqual(data["orders"],"DISABLED")

    def test_pressure_and_unreadable_memory_fail_closed(self):
        danger=profile(4096, 250)
        self.assertFalse(danger["heavy_research_allowed"])
        self.assertEqual(danger["state"],"MEMORY_PRESSURE")
        unknown=memory_snapshot(meminfo="MemTotal: 8192000 kB\n",
                                cgroup_limit=10000*MIB,cgroup_used=0)
        self.assertEqual(unknown["state"],"MEMORY_UNMEASURABLE")
        self.assertFalse(unknown["heavy_research_allowed"])

    def test_explicit_cgroup_limit_can_reduce_effective_budget(self):
        data=memory_snapshot(meminfo=meminfo(8192,4500),
                             cgroup_limit=3072*MIB,
                             cgroup_used=2900*MIB)
        self.assertEqual(data["tier"],"LOW")
        self.assertEqual(data["ram_available_mib"],172)
        self.assertEqual(data["state"],"MEMORY_PRESSURE")
        self.assertEqual(data["source"],"PROC_MEMINFO_AND_CGROUP_LIMIT")

    def test_batch_limits_shrink_only_optional_whitelisted_work(self):
        samples=[
            ("official_annual_pdf_source_index","--limit","10","3","6"),
            ("official_discovered_annual_catchup","--limit","25","8","15"),
            ("sector_refresh","--limit","300","100","200"),
            ("quarterly_refresh","--limit","600","150","300"),
            ("legacy_annual_verify","--limit","3","1","2"),
            ("archived_pdf_section_review","--max-files","2","1","1"),
        ]
        for step,flag,n,low,medium in samples:
            original=["python",step+".py",flag,n]
            self.assertEqual(adaptive_command(step,original,"LOW")[-1],low)
            self.assertEqual(adaptive_command(step,original,"MEDIUM")[-1],medium)
            self.assertEqual(adaptive_command(step,original,"HIGH"),original)
            self.assertEqual(original[-1],n)  # no mutation
        self.assertEqual(adaptive_command(
            "unrecognized_external_task",["python","not-a-task.py","--limit","40"],"LOW")[-1],
            "40")
        self.assertEqual(adaptive_command(
            "quarterly_refresh",["python","quarter.py","--limit","60"],"LOW")[-1],"60")

    def test_memory_deferred_maintenance_retries_after_25_minutes(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"runtime").mkdir()
            now=datetime(2026,10,17,18,tzinfo=timezone.utc)
            status=root/"runtime"/"maintenance_status.json"
            status.write_text(json.dumps({
                "state":"MEMORY_PRESSURE_DEFERRED",
                "updated_at":(now-timedelta(minutes=26)).isoformat()
            }))
            self.assertTrue(maintenance_due(root,now))
            status.write_text(json.dumps({
                "state":"MEMORY_PRESSURE_DEFERRED",
                "updated_at":(now-timedelta(minutes=10)).isoformat()
            }))
            self.assertFalse(maintenance_due(root,now))
            status.write_text(json.dumps({
                "state":"COMPLETED",
                "updated_at":(now-timedelta(minutes=26)).isoformat()
            }))
            self.assertFalse(maintenance_due(root,now))

    def test_low_ram_defers_before_starting_any_heavy_process(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            with (patch.object(termux_maintenance,"data_root",return_value=root),
                  patch.object(termux_maintenance,"memory_snapshot",
                               return_value=profile(2048,200)),
                  patch.object(termux_maintenance,"summary",return_value={}),
                  patch.object(termux_maintenance.subprocess,"run") as child):
                self.assertEqual(termux_maintenance.main(),0)
                child.assert_not_called()
            result=json.loads((root/"runtime"/"maintenance_status.json").read_text())
            self.assertEqual(result["state"],"MEMORY_PRESSURE_DEFERRED")
            self.assertEqual(result["steps"][0]["state"],"DEFERRED_LOW_RAM")
            self.assertFalse(result["memory_profile"]["heavy_research_allowed"])
            self.assertEqual(result["orders"],"MANUAL_ONLY")

    def test_memory_turns_low_between_steps_without_erasing_first_success(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            fake_steps=[
                ("quarterly_refresh",["python","quarter.py","--limit","600"],5),
                ("sector_refresh",["python","sector.py","--limit","300"],5),
            ]
            with (patch.object(termux_maintenance,"STEPS",fake_steps),
                  patch.object(termux_maintenance,"data_root",return_value=root),
                  patch.object(termux_maintenance,"memory_snapshot",
                               side_effect=[profile(3072,900),profile(3072,200)]),
                  patch.object(termux_maintenance,"summary",return_value={}),
                  patch.object(termux_maintenance.subprocess,"run",
                               return_value=SimpleNamespace(returncode=0)) as child):
                self.assertEqual(termux_maintenance.main(),0)
                self.assertEqual(child.call_count,1)
                self.assertEqual(child.call_args.args[0][-1],"150")
            result=json.loads((root/"runtime"/"maintenance_status.json").read_text())
            self.assertEqual(result["state"],"MEMORY_PRESSURE_DEFERRED")
            self.assertEqual([x["state"] for x in result["steps"]],
                             ["OK","DEFERRED_LOW_RAM"])
            self.assertEqual(result["orders"],"MANUAL_ONLY")


if __name__ == "__main__":
    unittest.main()
