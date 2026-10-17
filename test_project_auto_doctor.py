"""Proof that whole-project auto doctor fails closed on financial data and orders."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from research_monitor.auto_doctor import (
    evaluate,inspect,recoverable,optional_ai_advice,dashboard_ok,
    HEARTBEATS, STATUS,JOURNAL,MAX_RESTARTS_PER_COMPONENT_PER_WINDOW
)

NOW=datetime.fromisoformat("2026-10-17T10:30:00+00:00")
EPOCH=NOW.timestamp()


class FakeChild:
    def __init__(self,running=True):
        self.running=running
    def poll(self):
        return None if self.running else 1


class ProjectDoctorTests(unittest.TestCase):
    def setUp(self):
        d=TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root=Path(d.name)
        runtime=self.root/"runtime"
        runtime.mkdir()
        self.paths={
            "annual_historical_results.csv":"AUDITED_SOURCE_NOT_MUTATED",
            "FY2425.csv":"FY24_NOT_MUTATED",
            "NSE_FY2025-26_Consolidated_Fundamentals.csv":"FY25_NOT_MUTATED",
            "final_fundamental_shortlist.csv":"STRICT_NOT_MUTATED",
            "swing_watchlist.json":"NO_AUTO_TRADE",
        }
        for filename,content in self.paths.items():
            (self.root/filename).write_text(content)
        self.children={n:FakeChild() for n in (
            "worker","history","research","upstox_reference","dashboard"
        )}
        self.births={n:EPOCH-7200 for n in self.children}
        for name,(file,key,_) in HEARTBEATS.items():
            (runtime/file).write_text(json.dumps({
                key:(NOW-timedelta(seconds=40)).isoformat(),
                "state":"WAITING_NEXT_DAILY_WINDOW",
            }))
        self.runtime=runtime

    def same(self):
        for k,v in self.paths.items():
            self.assertEqual((self.root/k).read_text(),v)

    def test_every_supervised_component_is_monitored_and_healthy(self):
        report,fix=evaluate(self.root,self.children,self.births,
                            now=NOW,epoch=EPOCH,probe=lambda:True,
                            use_optional_ai=False)
        self.assertEqual(report["state"],"AUTO_DOCTOR_HEALTHY")
        self.assertEqual(report["issue_count"],0)
        self.assertEqual(fix,[])
        self.assertEqual(report["orders"],"DISABLED")
        self.same()

    def test_market_worker_restart_only_after_two_separate_stale_checks(self):
        (self.runtime/"worker_status.json").write_text(json.dumps({
            "updated_at":(NOW-timedelta(minutes=40)).isoformat(),
            "phase":"LIVE_MONITORING",
        }))
        once,first=evaluate(self.root,self.children,self.births,
                            now=NOW,epoch=EPOCH,probe=lambda:True,
                            use_optional_ai=False)
        self.assertEqual(first,[])
        self.assertEqual(once["issues"][0]["code"],
                         "SUPERVISED_WORKER_HEARTBEAT_STALE")
        later=NOW+timedelta(seconds=120)
        twice,recover=evaluate(self.root,self.children,self.births,
                               now=later,epoch=later.timestamp(),
                               probe=lambda:True,use_optional_ai=False)
        self.assertEqual(recover,["worker"])
        self.assertEqual(twice["automatic_recovery_requested"],["worker"])
        third,repeat=evaluate(self.root,self.children,self.births,
                              now=later+timedelta(seconds=120),
                              epoch=later.timestamp()+120,
                              probe=lambda:True,use_optional_ai=False)
        self.assertEqual(repeat,[])
        self.same()

    def test_heavy_after_close_analysis_not_interrupted(self):
        (self.runtime/"worker_status.json").write_text(json.dumps({
            "updated_at":(NOW-timedelta(minutes=40)).isoformat(),
            "phase":"AFTER_MARKET_ANALYSIS","analysis":"RUNNING",
        }))
        issues=inspect(self.root,self.children,self.births,now=NOW,
                       epoch=EPOCH,probe=lambda:True)
        self.assertFalse(any(x["component"]=="worker" for x in issues))

    def test_restart_storm_has_strict_six_hour_cap(self):
        issue={"code":"SUPERVISED_WORKER_HEARTBEAT_STALE","component":"history",
               "auto_repair":True}
        journal={}
        actions_seen=[]
        for j in range(12):
            epoch=EPOCH+j*120
            actions,journal=recoverable([issue],journal,epoch=epoch)
            actions_seen.extend(actions)
        self.assertLessEqual(len(actions_seen),MAX_RESTARTS_PER_COMPONENT_PER_WINDOW)
        self.assertGreaterEqual(len(actions_seen),1)

    def test_child_start_grace_suppresses_false_restart_of_old_status(self):
        self.births["history"]=EPOCH-30
        (self.runtime/"history_worker_status.json").write_text(json.dumps({
            "updated_at":(NOW-timedelta(days=2)).isoformat()
        }))
        issues=inspect(self.root,self.children,self.births,now=NOW,
                       epoch=EPOCH,probe=lambda:True)
        self.assertFalse(any(x["component"]=="history" for x in issues))

    def test_blocked_nse_source_cannot_be_auto_retried_or_faked(self):
        (self.runtime/"mainboard_raw_research_history_status.json").write_text(
            json.dumps({"state":"NSE_ACCESS_BLOCKED","source_blocked":True}))
        (self.runtime/"nse_official_annual_pdf_collector_status.json").write_text(
            json.dumps({"state":"NSE_OFFICIAL_ANNUAL_INDEX_ACCESS_BLOCKED"}))
        report,fix=evaluate(self.root,self.children,self.births,
                            now=NOW,epoch=EPOCH,probe=lambda:True,
                            use_optional_ai=False)
        self.assertEqual(fix,[])
        self.assertIn("SOURCE_BACKFILL_BLOCKED",
                      [x["code"] for x in report["issues"]])
        self.assertIn("ANNUAL_SOURCE_BLOCKED",
                      [x["code"] for x in report["issues"]])
        self.assertTrue(all(x["auto_repair"] is False for x in report["issues"]))
        self.same()

    def test_loopback_dashboard_probe_sends_configured_trusted_host(self):
        # The socket binds 127.0.0.1, while Flask accepts only Host: localhost.
        # Avoid false 400 health results and unnecessary supervised restarts.
        class HealthResponse:
            status=200
            def __enter__(self):
                return self
            def __exit__(self,*args):
                return False
        with patch("research_monitor.auto_doctor.urlopen",
                   return_value=HealthResponse()) as fetch:
            self.assertTrue(dashboard_ok())
        request=fetch.call_args.args[0]
        self.assertEqual(request.full_url,"http://127.0.0.1:8787/healthz")
        self.assertEqual(request.get_header("Host"),"localhost")
        self.assertEqual(fetch.call_args.kwargs["timeout"],1.5)
        self.same()

    def test_local_mfa_dashboard_failure_needs_confirmations(self):
        _,one=evaluate(self.root,self.children,self.births,
                       now=NOW,epoch=EPOCH,probe=lambda:False,
                       use_optional_ai=False)
        self.assertEqual(one,[])
        later=NOW+timedelta(seconds=120)
        _,two=evaluate(self.root,self.children,self.births,
                       now=later,epoch=later.timestamp(),probe=lambda:False,
                       use_optional_ai=False)
        self.assertEqual(two,["dashboard"])
        self.same()

    def test_optional_ai_disabled_never_makes_network_requests(self):
        issue={"code":"SOURCE_BACKFILL_BLOCKED","component":"data_source",
               "auto_repair":False}
        with patch.dict("os.environ",{"NSE_AI_DOCTOR_ENABLE":"0",
                                      "OPENAI_API_KEY":"synthetic-do-not-share"}),\
             patch("research_monitor.auto_doctor.requests.post") as req:
            advice=optional_ai_advice([issue],{},NOW)
        req.assert_not_called()
        self.assertEqual(advice["state"],
                         "EXTERNAL_AI_NOT_ENABLED_RULE_BASED_RECOVERY_RUNNING")

    def test_safe_model_advice_cannot_become_permission_to_execute(self):
        issue={"code":"DISK_HEADROOM_LOW","component":"storage",
               "auto_repair":False}
        fake={
            "status":"completed",
            "output":[{"type":"message","content":[
                {"type":"output_text","text":"Review disk space manually."}
            ]}],
        }
        class Response:
            status_code=200
            def json(self):
                return fake
        journal={}
        with patch.dict("os.environ",{
            "NSE_AI_DOCTOR_ENABLE":"1","OPENAI_API_KEY":"synthetic"
        }),patch("research_monitor.auto_doctor.requests.post",
                 return_value=Response()) as post:
            result=optional_ai_advice([issue],journal,NOW)
        self.assertEqual(result["state"],"AI_DIAGNOSIS_ADVICE_ONLY")
        self.assertIs(result["permission_to_execute_model_instructions"],False)
        self.assertEqual(post.call_args.kwargs["json"]["store"],False)
        self.assertNotIn("synthetic",json.dumps(post.call_args.kwargs["json"]))
        with patch.dict("os.environ",{
            "NSE_AI_DOCTOR_ENABLE":"1","OPENAI_API_KEY":"synthetic"
        }):
            self.assertEqual(optional_ai_advice([
                issue],journal,NOW+timedelta(minutes=1))[
                    "state"],"AI_DIAGNOSIS_RATE_LIMITED")
        self.same()


if __name__=="__main__":
    unittest.main()
