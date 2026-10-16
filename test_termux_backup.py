import tempfile
import unittest
from pathlib import Path

from cryptography.exceptions import InvalidTag

from termux_backup import create_backup, restore_backup


class TermuxBackupTests(unittest.TestCase):
    def test_encrypted_full_state_round_trip_and_stale_runtime_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = root / "code"
            data = root / "data"
            restored = root / "restored"
            code.mkdir()
            (data / "runtime").mkdir(parents=True)
            (code / ".env").write_text("APP_SECRET_KEY=secret\n", encoding="utf-8")
            (data / "important.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            (data / "runtime" / "auth.sqlite").write_bytes(b"auth")
            (data / "runtime" / "termux_supervisor.pid").write_text("123", encoding="utf-8")
            backup = root / "full.nsebak"

            create_backup(code, data, backup, "correct horse battery")
            self.assertTrue(backup.is_file())
            self.assertNotIn(b"APP_SECRET_KEY=secret", backup.read_bytes())

            new_code = root / "new-code"
            new_code.mkdir()
            result = restore_backup(new_code, restored, backup, "correct horse battery")

            self.assertEqual(
                (restored / "important.csv").read_text(encoding="utf-8"),
                "a,b\n1,2\n",
            )
            self.assertEqual((restored / "runtime" / "auth.sqlite").read_bytes(), b"auth")
            self.assertFalse((restored / "runtime" / "termux_supervisor.pid").exists())
            self.assertEqual(
                (new_code / ".env").read_text(encoding="utf-8"),
                "APP_SECRET_KEY=secret\n",
            )
            self.assertTrue(result["restored_env"])

    def test_wrong_passphrase_does_not_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = root / "code"
            data = root / "data"
            code.mkdir()
            data.mkdir()
            (data / "important.csv").write_text("value\n", encoding="utf-8")
            backup = root / "full.nsebak"
            create_backup(code, data, backup, "correct horse battery")

            with self.assertRaises(InvalidTag):
                restore_backup(
                    root / "other-code",
                    root / "other-data",
                    backup,
                    "wrong password here",
                )


if __name__ == "__main__":
    unittest.main()
