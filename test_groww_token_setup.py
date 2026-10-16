"""Security tests for private Groww token storage and no-live trade controls."""
import contextlib
import io
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from dotenv import dotenv_values

from groww_token_setup import KEY, main, save_groww_token

TOKEN="local_test_only_groww_token_0123456789"


class GrowwTokenSetupTests(unittest.TestCase):
    def test_preserves_existing_token_and_other_env_without_leaking(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/".env"
            path.write_text("UPSTOX_ANALYTICS_TOKEN='already-local-upstox'\nNSE_DATA_DIR='/home/user/private-data'\n")
            os.chmod(path,0o644)
            output=save_groww_token(path,TOKEN,saved_at="2026-10-16T09:00:00+00:00")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            self.assertNotIn(TOKEN,str(output))
            saved=dotenv_values(path)
            self.assertEqual(saved[KEY],TOKEN)
            self.assertEqual(saved["UPSTOX_ANALYTICS_TOKEN"],"already-local-upstox")
            self.assertEqual(saved["NSE_DATA_DIR"],"/home/user/private-data")
            self.assertEqual(output["order_execution"],"DISABLED")

    def test_rejects_symlink_and_multiline(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/".env"
            with self.assertRaisesRegex(ValueError,"FORMAT_INVALID"):
                save_groww_token(path,TOKEN+"\nPOST=order")
            self.assertFalse(path.exists())
            victim=Path(directory)/"other.txt"
            victim.write_text("unchanged")
            path.symlink_to(victim)
            with self.assertRaises(ValueError):
                save_groww_token(path,TOKEN)
            self.assertEqual(victim.read_text(),"unchanged")

    def test_hidden_prompt_offline_does_no_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/".env"
            stdout=io.StringIO()
            with patch("groww_token_setup.private_env_path",return_value=path), \
                 patch("groww_token_setup.getpass.getpass",return_value=TOKEN), \
                 patch("groww_token_setup.dual_quote_check") as checker, \
                 contextlib.redirect_stdout(stdout):
                status=main(["--offline"])
            self.assertEqual(status,0)
            self.assertEqual(dotenv_values(path)[KEY],TOKEN)
            self.assertNotIn(TOKEN,stdout.getvalue())
            checker.assert_not_called()

    def test_dual_check_uses_both_data_sources_and_keeps_execution_off(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/".env"
            save_groww_token(path,TOKEN)
            dummy={
                "groww":{"quote_status":"OK_FRESH"},
                "upstox":{"quote_status":"REJECTED_STALE_OR_FUTURE_QUOTE"},
                "live_orders":"DISABLED",
            }
            with patch("groww_token_setup.private_env_path",return_value=path), \
                 patch("groww_token_setup.dual_quote_check",return_value=dummy) as check, \
                 patch("groww_token_setup.atomic_status") as atomic, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                status=main(["--check"])
            self.assertEqual(status,0)
            check.assert_called_once()
            atomic.assert_called_once()
            self.assertNotIn(TOKEN,output.getvalue())


if __name__=="__main__":
    unittest.main()
