"""Synthetic Upstox auth/setup tests; no token or network to CI."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from dotenv import dotenv_values

from research_monitor.upstox_data import DataUnavailable
from upstox_token_setup import (
    ENV_KEY, TOKEN_KIND_KEY, RELIANCE_KEY, connectivity_report,
    main, store_token,
)


TEST_SECRET = "synthetic_private_testing_token_ABCD0123456"


class FakeUpstox:
    token = TEST_SECRET
    def __init__(self, response=None, error_at=None):
        self.calls = []
        self.error_at = error_at
        self.response = response
    def _read(self, path, params=None):
        self.calls.append((path,params))
        if path == self.error_at:
            raise DataUnavailable("UPSTOX_HTTP_401")
        if path == "market/status/NSE":
            return {"exchange": "NSE", "status": "CLOSED"}
        if path == "market-quote/quotes":
            return {"NSE_EQ:RELIANCE": {
                "instrument_token": RELIANCE_KEY, "last_price": 1450.25
            }}
        raise AssertionError("Unexpected route: " + path)


class TokenSetupTests(unittest.TestCase):
    def test_private_file_mode_preserves_other_secrets_and_no_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/".env"
            path.write_text("NSE_DATA_DIR='/some/private/data'\nTEST_OTHER_KEY='other'\n")
            os.chmod(path,0o644)
            report = store_token(path,TEST_SECRET,"analytics",
                                 saved_at="2026-10-16T08:00:00+00:00")
            self.assertTrue(report["saved"])
            self.assertNotIn(TEST_SECRET,str(report))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            values=dotenv_values(path)
            self.assertEqual(values[ENV_KEY],TEST_SECRET)
            self.assertEqual(values[TOKEN_KIND_KEY],"analytics")
            self.assertEqual(values["TEST_OTHER_KEY"],"other")
            self.assertEqual(values["NSE_DATA_DIR"],"/some/private/data")
            updated=store_token(path,"replacement_hidden_token_XYZ00112233")
            self.assertNotIn(TEST_SECRET,path.read_text())
            self.assertEqual(
                dotenv_values(path)[ENV_KEY],
                "replacement_hidden_token_XYZ00112233",
            )
            self.assertNotIn(TEST_SECRET,str(updated))

    def test_rejects_shell_history_injection_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/".env"
            for token in ("short","invalid\nOHLCV=bad", "a b" + "x"*40,
                          "Bearer " + TEST_SECRET,"x"*8193):
                with self.subTest(token_length=len(token)):
                    with self.assertRaises(ValueError) as e:
                        store_token(path,token)
                    self.assertNotIn(token,str(e.exception))
            self.assertFalse(path.exists())
            target=Path(directory)/"victim"
            target.write_text("DO_NOT_TOUCH")
            path.symlink_to(target)
            with self.assertRaises(ValueError):
                store_token(path,TEST_SECRET)
            self.assertEqual(target.read_text(),"DO_NOT_TOUCH")

    def test_standard_type_does_not_promise_one_year(self):
        with tempfile.TemporaryDirectory() as folder:
            result=store_token(Path(folder)/".env",TEST_SECRET,"standard")
            self.assertEqual(result["token_type"],"standard")
            self.assertIn("expire", result["warning"])

    def test_closed_market_is_successful_auth_but_not_live_feed(self):
        dummy=FakeUpstox()
        report=connectivity_report(dummy)
        self.assertEqual(report["market_status_endpoint"],"OK")
        self.assertEqual(report["exchange_market_status"],"CLOSED")
        self.assertEqual(report["known_nse_equity_quote_endpoint"],"OK")
        self.assertFalse(report["live_quote_verified"])
        self.assertEqual(report["orders"],"DISABLED")
        self.assertEqual(dummy.calls,[
            ("market/status/NSE",None),
            ("market-quote/quotes",{"instrument_key": RELIANCE_KEY}),
        ])
        self.assertNotIn(TEST_SECRET,json.dumps(report))

    def test_http_error_is_not_logged_with_secret(self):
        dummy=FakeUpstox(error_at="market/status/NSE")
        report=connectivity_report(dummy)
        self.assertEqual(report["market_status_endpoint"],"UPSTOX_HTTP_401")
        self.assertEqual(report["known_nse_equity_quote_endpoint"],"NOT_CHECKED")
        self.assertNotIn(TEST_SECRET,json.dumps(report))

    def test_quote_instrument_mismatch_fails_closed(self):
        class Incorrect(FakeUpstox):
            def _read(self, path,params=None):
                if path == "market/status/NSE":
                    return {"exchange":"NSE","status":"NORMAL_OPEN"}
                return {"a": {"instrument_token":"NSE_EQ|INCORRECT",
                              "last_price":100}}
        result=connectivity_report(Incorrect())
        self.assertEqual(result["known_nse_equity_quote_endpoint"],
                         "INSTRUMENT_OR_PRICE_NOT_VERIFIED")
        self.assertFalse(result["live_quote_verified"])

    def test_local_hidden_prompt_and_offline_no_http(self):
        with tempfile.TemporaryDirectory() as folder:
            env_file=Path(folder)/".env"
            stdout=io.StringIO()
            with patch("upstox_token_setup.private_env_path",
                       return_value=env_file), patch(
                "upstox_token_setup.getpass.getpass",
                return_value=TEST_SECRET,
            ), patch("upstox_token_setup.connectivity_report") as network:
                with contextlib.redirect_stdout(stdout):
                    status=main(["--offline"])
            self.assertEqual(status,0)
            network.assert_not_called()
            self.assertNotIn(TEST_SECRET,stdout.getvalue())
            self.assertEqual(dotenv_values(env_file)[ENV_KEY],TEST_SECRET)


if __name__ == "__main__":
    unittest.main()
