"""Fail-closed independent Android Termux supervisor watchdog regressions."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import json
import os
import signal
import unittest

import termux_watchdog as wd


class TermuxWatchdogTests(unittest.TestCase):
    def test_single_failure_not_restarted_and_two_separated_required(self):
        allowed, journal, why = wd.evaluate_recovery(
            "SUPERVISOR_MISSING", {}, epoch=1000)
        self.assertFalse(allowed)
        self.assertEqual(why, "AWAITING_SECOND_CONFIRMATION")
        allowed, journal, why = wd.evaluate_recovery(
            "SUPERVISOR_MISSING", journal, epoch=1120)
        self.assertTrue(allowed)
        self.assertEqual(why, "SAFE_RESTART_AUTHORIZED")
        self.assertEqual(journal["recoveries"], [1120])
        self.assertEqual(journal["incident"], {})

    def test_cooldown_max_twice_in_six_hours(self):
        journal = {"recoveries": [1000], "incident": {}}
        _, journal, _ = wd.evaluate_recovery("SUPERVISOR_MISSING", journal,
                                             epoch=1120)
        allowed, journal, why = wd.evaluate_recovery("SUPERVISOR_MISSING", journal,
                                                     epoch=1240)
        self.assertFalse(allowed)
        self.assertEqual(why, "RESTART_COOLDOWN")
        # Force independent new first/second confirmation once cooldown passes.
        _, journal, _ = wd.evaluate_recovery("SUPERVISOR_MISSING", journal,
                                             epoch=2880)
        allowed, journal, why = wd.evaluate_recovery("SUPERVISOR_MISSING", journal,
                                                     epoch=3000)
        self.assertTrue(allowed)
        self.assertEqual(len(journal["recoveries"]), 2)
        _, journal, _ = wd.evaluate_recovery("SUPERVISOR_MISSING", journal,
                                             epoch=3120)
        allowed, _, why = wd.evaluate_recovery("SUPERVISOR_MISSING", journal,
                                               epoch=3240)
        self.assertFalse(allowed)
        self.assertEqual(why, "SIX_HOUR_RESTART_CAP_REACHED")

    def test_recovered_status_resets_pending_confirmation(self):
        _, old, _ = wd.evaluate_recovery("SUPERVISOR_HEARTBEAT_STALE", {},
                                         epoch=1000)
        good, new, why = wd.evaluate_recovery("HEALTHY", old, epoch=1120)
        self.assertFalse(good)
        self.assertEqual(new["incident"], {})
        self.assertEqual(why, "NOT_RECOVERABLE")
        allowed, _, _ = wd.evaluate_recovery("SUPERVISOR_HEARTBEAT_STALE",
                                             new, epoch=1240)
        self.assertFalse(allowed)

    def test_unverified_pid_or_corrupt_status_not_restartable(self):
        for issue in ("SUPERVISOR_IDENTITY_UNVERIFIED",
                      "SUPERVISOR_STATUS_UNVERIFIED"):
            allowed, _, reason = wd.evaluate_recovery(issue, {}, epoch=1000)
            self.assertFalse(allowed, issue)
            self.assertEqual(reason, "NOT_RECOVERABLE")

    def test_exact_same_user_supervisor_script_required(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            entry=root/"123"
            entry.mkdir()
            (entry/"cmdline").write_bytes(
                ("python\\0"+str(wd.CODE_ROOT/"termux_supervisor.py")+"\\0")
                .replace("\\0","\x00").encode())
            with patch.object(Path, "stat", return_value=SimpleNamespace(
                    st_uid=os.getuid())):
                self.assertEqual(wd.process_identity(123,proc=root),"EXPECTED")
                (entry/"cmdline").write_bytes(
                    b"python\x00/tmp/suspicious-termux_supervisor.py\x00")
                self.assertEqual(wd.process_identity(123,proc=root),"UNVERIFIED")
            self.assertEqual(wd.process_identity(987,proc=root),"NOT_RUNNING")

    def test_supervisor_heartbeat_requires_matching_pid_and_fresh_timestamp(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            runtime=root/"runtime"
            runtime.mkdir()
            (runtime/"termux_supervisor.pid").write_text("123")
            status=runtime/"termux_supervisor_status.json"
            status.write_text(json.dumps({
                "updated_at":1000.0,"supervisor_pid":123,"state":"RUNNING"
            }))
            with patch.object(wd,"process_identity",return_value="EXPECTED"):
                self.assertEqual(wd.supervisor_probe(root,epoch=1100)[0],
                                 "HEALTHY")
                self.assertEqual(wd.supervisor_probe(root,epoch=1300)[0],
                                 "SUPERVISOR_HEARTBEAT_STALE")
                status.write_text(json.dumps({
                    "updated_at":1000.0,"supervisor_pid":999,
                }))
                self.assertEqual(wd.supervisor_probe(root,epoch=1300)[0],
                                 "SUPERVISOR_STATUS_UNVERIFIED")

    def test_unverified_stale_pid_never_signaled(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            (root/"runtime").mkdir()
            with (patch.object(wd,"process_identity",return_value="UNVERIFIED"),
                  patch.object(wd.os,"kill") as kill,
                  patch.object(wd.subprocess,"run") as run):
                state=wd.recover_supervisor(
                    root,"SUPERVISOR_HEARTBEAT_STALE",123)
            self.assertEqual(state,"RECHECK_IDENTITY_FAILED_NO_SIGNAL")
            kill.assert_not_called()
            run.assert_not_called()

    def test_missing_backend_not_started_when_memory_critical(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            rt=root/"runtime"
            rt.mkdir()
            (rt/"termux_watchdog.enabled").write_text("enabled")
            with (patch.object(wd,"supervisor_probe",
                               return_value=("SUPERVISOR_MISSING",None)),
                  patch.object(wd,"memory_snapshot",
                               return_value={"heavy_research_allowed":False}),
                  patch.object(wd.subprocess,"run") as run):
                result=wd.recover_supervisor(root,"SUPERVISOR_MISSING",None)
            self.assertEqual(result,"LOW_MEMORY_NO_RESTART")
            run.assert_not_called()

    def test_missing_supervisor_safe_start_after_confirmed_budget(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            rt=root/"runtime"
            rt.mkdir()
            (rt/"termux_watchdog.enabled").write_text("enabled")
            with (patch.object(wd,"supervisor_probe",
                               return_value=("SUPERVISOR_MISSING",None)),
                  patch.object(wd,"memory_snapshot",
                               return_value={"heavy_research_allowed":True}),
                  patch.object(wd.subprocess,"run",
                               return_value=SimpleNamespace(returncode=0)) as run):
                result=wd.recover_supervisor(root,"SUPERVISOR_MISSING",None)
            self.assertEqual(result,"SAFE_START_REQUESTED")
            args=run.call_args.args[0]
            self.assertEqual(args[-1],"start")
            self.assertEqual(run.call_args.kwargs["env"]["NSE_DATA_DIR"],str(root))

    def test_stale_supervisor_only_signals_verified_pid_and_never_kill(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            rt=root/"runtime"
            rt.mkdir()
            (rt/"termux_watchdog.enabled").write_text("enabled")
            with (patch.object(wd,"process_identity",
                               side_effect=["EXPECTED","NOT_RUNNING"]),
                  patch.object(wd,"supervisor_probe",
                               return_value=("SUPERVISOR_HEARTBEAT_STALE",123)),
                  patch.object(wd.os,"kill") as kill,
                  patch.object(wd,"memory_snapshot",
                               return_value={"heavy_research_allowed":True}),
                  patch.object(wd.subprocess,"run",
                               return_value=SimpleNamespace(returncode=0))):
                result=wd.recover_supervisor(
                    root,"SUPERVISOR_HEARTBEAT_STALE",123)
            self.assertEqual(result,"SAFE_START_REQUESTED")
            kill.assert_called_once_with(123,signal.SIGTERM)

    def test_script_explicit_stop_disables_watchdog_before_supervisor_stop(self):
        script=(wd.CODE_ROOT/"termux_backend.sh").read_text()
        self.assertIn('stop_backend() {\n  stop_watchdog',script)
        self.assertIn('    start_watchdog\n',script)
        self.assertIn('termux_watchdog.enabled',script)
        self.assertIn('termux_watchdog.py',script)


if __name__=="__main__":
    unittest.main()
