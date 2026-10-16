"""MFA+CSRF control: only user Yes/No and exact integer qty for paper plans."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

import app as dashboard
from research_monitor.assistant_trade_queue import ApprovalQueue


class AssistantDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        root_patch=patch.object(dashboard,"ROOT",self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.app=dashboard.create_app({
            "APP_SECRET_KEY":"x"*48,
            "APP_PASSWORD_HASH":generate_password_hash("dev-only-password"),
            "APP_TOTP_SECRET":"JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
            "APP_AUTH_DB":str(self.root/"auth.sqlite"),
            "APP_HOST":"localhost",
        })
        self.client=self.app.test_client()
        self.plan_params={
            "symbol":"DEMO","signal_day":"2026-10-16",
            "entry_min":100,"entry_max":101,"stop":95,
            "target_1":112,"target_2":118,"capital_inr":100000,
            "source_sha":"d"*64,"signal_price":100.5,
        }

    def login_test(self):
        fake=patch.object(self.app.extensions["auth_store"],"valid",
                          return_value=True)
        fake.start()
        self.addCleanup(fake.stop)
        with self.client.session_transaction() as sess:
            sess["auth"]="dummy"
            sess["csrf"]="test-csrf"

    def make_plan(self):
        return ApprovalQueue(
            self.root/"runtime"/"assistant_trade_queue.sqlite3"
        ).propose(**self.plan_params)

    def test_mfa_is_required_and_get_does_not_create_broker_session(self):
        r=self.client.get("/assistant",base_url="https://localhost")
        self.assertEqual(r.status_code,302)
        self.assertFalse(
            (self.root/"runtime"/"assistant_trade_queue.sqlite3").exists()
        )

    def test_authenticated_yes_qty_creates_exactly_one_paper_decision(self):
        self.login_test()
        plan=self.make_plan()
        url="/assistant/"+plan["id"]
        reviewed=self.client.get(url,base_url="https://localhost")
        self.assertEqual(reviewed.status_code,200)
        self.assertIn(b"Yes",reviewed.data)
        self.assertIn(b"Quantity",reviewed.data)
        self.assertIn(b"Groww live orders are disabled",reviewed.data)
        info=dict(
            csrf="test-csrf",
            fingerprint=plan["fingerprint"],
            answer="yes",quantity="2",
        )
        result=self.client.post(url+"/decide",data=info,
                                base_url="https://localhost")
        self.assertEqual(result.status_code,302)
        store=ApprovalQueue(
            self.root/"runtime"/"assistant_trade_queue.sqlite3"
        )
        actual=store.get(plan["id"])
        self.assertEqual(actual["state"],"ARMED_PAPER_ONLY")
        self.assertEqual(actual["quantity"],2)
        self.assertFalse(actual["live_trade_authorized"])
        self.assertEqual(store.list_recent()[0]["id"],plan["id"])
        replay=self.client.post(url+"/decide",data=info,
                                base_url="https://localhost")
        self.assertEqual(replay.status_code,409)

    def test_no_requires_no_quantity_and_blocks_paper_entry(self):
        self.login_test()
        plan=self.make_plan()
        response=self.client.post(
            f"/assistant/{plan['id']}/decide",data={
                "csrf":"test-csrf","fingerprint":plan["fingerprint"],
                "answer":"no"
            },base_url="https://localhost",
        )
        self.assertEqual(response.status_code,302)
        state=ApprovalQueue(
            self.root/"runtime"/"assistant_trade_queue.sqlite3"
        ).get(plan["id"])
        self.assertEqual(state["state"],"REJECTED")

    def test_csrf_does_not_modify_approval(self):
        self.login_test()
        plan=self.make_plan()
        r=self.client.post(
            f"/assistant/{plan['id']}/decide",data={
                "csrf":"wrong",
                "fingerprint":plan["fingerprint"],
                "answer":"yes","quantity":"1"
            },base_url="https://localhost"
        )
        self.assertEqual(r.status_code,403)
        state=ApprovalQueue(
            self.root/"runtime"/"assistant_trade_queue.sqlite3"
        ).get(plan["id"])
        self.assertEqual(state["state"],"WAITING_FOR_OWNER")

    def test_huge_qty_refused_html_safe_no_live_orders(self):
        self.login_test()
        plan=self.make_plan()
        r=self.client.post(
            f"/assistant/{plan['id']}/decide",data={
                "csrf":"test-csrf",
                "fingerprint":plan["fingerprint"],
                "answer":"yes","quantity":"9999999"
            },base_url="https://localhost"
        )
        self.assertEqual(r.status_code,409)
        self.assertIn(b"paper",r.data.lower())
        self.assertFalse(b"AUTH_TOKEN" in r.data)

    def test_index_uses_paper_status_no_order_buttons(self):
        self.login_test()
        r=self.client.get("/assistant",base_url="https://localhost")
        self.assertEqual(r.status_code,200)
        self.assertIn(b"No eligible, fresh strategy-generated",r.data)
        self.assertNotIn(b"/v1/order/create",r.data)
        home=self.client.get("/",base_url="https://localhost")
        self.assertIn(b"/assistant",home.data)


if __name__=="__main__":
    unittest.main()
