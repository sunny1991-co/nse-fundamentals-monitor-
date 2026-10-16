import io
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

from swing_nse_history import (
    Action,
    adjust_frame,
    candidate_urls,
    classify_action,
    init_db,
    intervals_cover,
    parse_bhavcopy,
    suspicious_no_file_gap,
    terminal_day_coverage,
)


class NseHistoryTests(unittest.TestCase):
    def test_legacy_url_preserves_required_path_casing(self):
        urls = candidate_urls(date(2023, 4, 17))
        legacy = dict(urls)["legacy"]
        self.assertEqual(
            legacy,
            "https://nsearchives.nseindia.com/content/historical/EQUITIES/2023/APR/cm17APR2023bhav.csv.zip",
        )

    def test_parse_legacy_zip_filters_eq_and_target_symbols(self):
        csv_text = (
            "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY,TIMESTAMP\n"
            "ABC,EQ,100,110,95,105,1234,17-APR-2023\n"
            "ABC,BE,90,91,89,90,10,17-APR-2023\n"
            "OTHER,EQ,10,11,9,10,100,17-APR-2023\n"
        )
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("cm17APR2023bhav.csv", csv_text)
        rows = parse_bhavcopy(payload.getvalue(), date(2023, 4, 17), "legacy", {"ABC"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:2], ("ABC", "2023-04-17"))
        self.assertEqual(rows[0][2:], (100.0, 110.0, 95.0, 105.0, 1234.0))

    def test_parse_rejects_wrong_file_date(self):
        raw = (
            "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY,TIMESTAMP\n"
            "ABC,EQ,100,110,95,105,1234,18-APR-2023\n"
        ).encode()
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as zf:
            zf.writestr("wrong.csv", raw)
        with self.assertRaisesRegex(ValueError, "BHAVCOPY_DATE_MISMATCH"):
            parse_bhavcopy(payload.getvalue(), date(2023, 4, 17), "legacy", {"ABC"})

    def test_corporate_action_classification_is_fail_closed(self):
        bonus = classify_action("ABC", date(2024, 1, 10), "Bonus 1:1")
        self.assertTrue(bonus.supported)
        self.assertAlmostEqual(bonus.factor, 2.0)

        split = classify_action(
            "ABC",
            date(2024, 2, 10),
            "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share",
        )
        self.assertTrue(split.supported)
        self.assertAlmostEqual(split.factor, 5.0)

        rights = classify_action("ABC", date(2024, 3, 10), "Rights Issue 1:4")
        self.assertFalse(rights.supported)
        self.assertEqual(rights.kind, "COMPLEX_UNSUPPORTED")

        dividend = classify_action("ABC", date(2024, 4, 10), "Interim Dividend - Rs 5 Per Share")
        self.assertTrue(dividend.supported)
        self.assertEqual(dividend.factor, 1.0)

        unknown = classify_action("ABC", date(2024, 5, 10), "Other Corporate Purpose")
        self.assertFalse(unknown.supported)
        self.assertEqual(unknown.kind, "UNRECOGNIZED_ACTION_REVIEW_REQUIRED")

        malformed_bonus = classify_action("ABC", date(2024, 6, 10), "Issue of Bonus Shares")
        self.assertFalse(malformed_bonus.supported)
        self.assertEqual(malformed_bonus.kind, "SHARE_UNIT_ACTION_UNPARSED")

    def test_adjustment_changes_only_bars_before_ex_date(self):
        frame = pd.DataFrame(
            [
                ["ABC", "2024-01-01", 100, 110, 90, 105, 1000],
                ["ABC", "2024-01-10", 52, 55, 50, 53, 2100],
                ["XYZ", "2024-01-01", 20, 22, 19, 21, 500],
            ],
            columns=["Symbol", "Date", "Open", "High", "Low", "Close", "Volume"],
        )
        action = Action("ABC", date(2024, 1, 10), "Bonus 1:1", "BONUS", 2.0, True)
        out = adjust_frame(frame, [action])
        old = out[(out.Symbol == "ABC") & (out.Date == "2024-01-01")].iloc[0]
        ex = out[(out.Symbol == "ABC") & (out.Date == "2024-01-10")].iloc[0]
        self.assertEqual(old.Open, 50)
        self.assertEqual(old.Volume, 2000)
        self.assertEqual(ex.Open, 52)

    def test_interval_coverage_requires_no_gap(self):
        self.assertTrue(
            intervals_cover(
                [("2024-01-01", "2024-06-30"), ("2024-07-01", "2024-12-31")],
                date(2024, 1, 1),
                date(2024, 12, 31),
            )
        )
        self.assertFalse(
            intervals_cover(
                [("2024-01-01", "2024-06-29"), ("2024-07-01", "2024-12-31")],
                date(2024, 1, 1),
                date(2024, 12, 31),
            )
        )

    def test_five_consecutive_weekday_404s_never_count_as_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            db = init_db(Path(directory) / "history.sqlite")
            try:
                days = [
                    date(2026, 10, 5),
                    date(2026, 10, 6),
                    date(2026, 10, 7),
                    date(2026, 10, 8),
                    date(2026, 10, 9),
                ]
                for d in days:
                    db.execute(
                        "INSERT INTO days(day,status,source_url,source_sha256,rows,checked_at,error) VALUES(?,?,?,?,?,?,?)",
                        (d.isoformat(), "NO_FILE", "", "", 0, "2026-10-10T00:00:00+00:00", "404"),
                    )
                db.commit()
                self.assertTrue(suspicious_no_file_gap(db, days[0], days[-1]))
                self.assertFalse(terminal_day_coverage(db, days[0], days[-1]))
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
