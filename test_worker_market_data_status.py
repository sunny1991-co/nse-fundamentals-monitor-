"""Synthetic schedule and broker-status checks. Never contact a broker or trade."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime

from project_status import snapshot
from swing_worker import tick


NOW = datetime.fromisoformat("2026-10-16T16:00:00+05:30")


def calendar():
    return {
        "verified": True,
        "source": "SYNTHETIC_TEST_ONLY",
        "valid_from": "2026-10-16",
        "valid_to": "2026-10-16",
        "sessions": {
            "2026-10-16": {
                "open": "2026-10-16T09:15:00+05:30",
                "close": "2026-10-16T15:30:00+05:30",
            }
        },
    }


class MarketDataStatusTests(unittest.TestCase):
    def test_upstox_saved_after_market_not_reported_as_live_or_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"exchange_sessions.json").write_text(json.dumps(calendar()))
            with patch.dict(os.environ,{
                "UPSTOX_ANALYTICS_TOKEN":"synthetic_mock_secret_never_logged",
                "GROWW_ACCESS_TOKEN":"",
            }),patch("swing_worker.claim_job",return_value=False):
                status=tick(root,NOW)
            self.assertEqual(status["phase"],"AFTER_MARKET_ANALYSIS")
            self.assertTrue(status["upstox_analytics_token_configured"])
            self.assertFalse(status["groww_api_token_configured"])
            self.assertEqual(status["live_feed"],"SESSION_NOT_OPEN_NO_LIVE_QUOTE")
            self.assertEqual(status["orders"],"MANUAL_ONLY")
            status_text=(root/"runtime"/"worker_status.json").read_text()
            self.assertNotIn("synthetic_mock_secret_never_logged",status_text)
            project=snapshot(root)
            self.assertEqual(
                project["upstox_token_status"],
                "CONFIGURED_READ_ONLY_NOT_LIVE_VERIFIED",
            )
            self.assertEqual(
                project["groww_token_status"],
                "NOT_CONFIGURED_OR_WORKER_NOT_UPDATED",
            )
            self.assertEqual(project["broker_market_data_priority"],
                             ["GROWW","UPSTOX"])
            self.assertEqual(project["strategy_trading_enabled"],False)
            self.assertEqual(project["automatic_groww_order_execution"],
                             "DISABLED_NO_VALIDATED_STRATEGY")

    def test_groww_configured_does_not_enable_orders_after_market(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"exchange_sessions.json").write_text(json.dumps(calendar()))
            with patch.dict(os.environ,{
                "GROWW_ACCESS_TOKEN":"synthetic_mock_groww_token",
                "UPSTOX_ANALYTICS_TOKEN":"",
            }), patch("swing_worker.claim_job",return_value=False):
                status=tick(root,NOW)
            self.assertTrue(status["groww_api_token_configured"])
            self.assertFalse(status["upstox_analytics_token_configured"])
            self.assertEqual(status["live_feed"],"SESSION_NOT_OPEN_NO_LIVE_QUOTE")
            self.assertEqual(status["orders"],"MANUAL_ONLY")
            self.assertNotIn(
                "synthetic_mock_groww_token",
                (root/"runtime"/"worker_status.json").read_text(),
            )


if __name__=="__main__":
    unittest.main()
