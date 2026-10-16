"""Reviewed NSE capital-market dates used to audit historical bhavcopy gaps.

This is intentionally static and fail-closed. It contains full-day weekday
closures reviewed from NSE capital-market holiday circulars for 2020-2026 and
explicit non-weekday/special sessions that must have a bhavcopy rather than be
silently treated as a weekend.

Primary NSE references:
- 2020: NSE/CMTR/42877 (11-Dec-2019)
- 2021: NSE/CMTR/46623
- 2022: NSE/CMTR/50560
- 2023: NSE/CMTR/54757 plus NSE/CMTR/57285 for the revised Bakri Id holiday
- 2024: NSE/CMTR/59722, NSE/CMTR/60338 for 22-Jan-2024, NSE/CMTR/61516 for 20-May-2024, and NSE/CMTR/64960 for 20-Nov-2024
- 2025: NSE/CMTR/65587
- 2026: NSE/CMTR/71775 plus the current NSE Market Timings & Holidays page
Special-session references include NSE/MSD/59999, NSE/MSD/60677,
NSE/MSD/66381 and NSE/CMTR/72349.

Muhurat dates are trading sessions, not accepted as missing-file holidays.
"""
from __future__ import annotations

from datetime import date

REVIEWED_AT = "2026-10-16"

FULL_DAY_NONTRADING = {
    # 2020
    date(2020, 2, 21), date(2020, 3, 10), date(2020, 4, 2),
    date(2020, 4, 6), date(2020, 4, 10), date(2020, 4, 14),
    date(2020, 5, 1), date(2020, 5, 25), date(2020, 10, 2),
    date(2020, 11, 16), date(2020, 11, 30), date(2020, 12, 25),
    # 2021 (04-Nov was Muhurat Trading, so it is deliberately excluded)
    date(2021, 1, 26), date(2021, 3, 11), date(2021, 3, 29),
    date(2021, 4, 2), date(2021, 4, 14), date(2021, 4, 21),
    date(2021, 5, 13), date(2021, 7, 21), date(2021, 8, 19),
    date(2021, 9, 10), date(2021, 10, 15), date(2021, 11, 5),
    date(2021, 11, 19),
    # 2022 (24-Oct was Muhurat Trading)
    date(2022, 1, 26), date(2022, 3, 1), date(2022, 3, 18),
    date(2022, 4, 14), date(2022, 4, 15), date(2022, 5, 3),
    date(2022, 8, 9), date(2022, 8, 15), date(2022, 8, 31),
    date(2022, 10, 5), date(2022, 10, 26), date(2022, 11, 8),
    # 2023
    date(2023, 1, 26), date(2023, 3, 7), date(2023, 3, 30),
    date(2023, 4, 4), date(2023, 4, 7), date(2023, 4, 14),
    date(2023, 5, 1), date(2023, 6, 29), date(2023, 8, 15),
    date(2023, 9, 19), date(2023, 10, 2), date(2023, 10, 24),
    date(2023, 11, 14), date(2023, 11, 27), date(2023, 12, 25),
    # 2024 (01-Nov was Muhurat Trading; 22-Jan was an added closure)
    date(2024, 1, 22), date(2024, 1, 26), date(2024, 3, 8),
    date(2024, 3, 25), date(2024, 3, 29), date(2024, 4, 11),
    date(2024, 4, 17), date(2024, 5, 1), date(2024, 5, 20), date(2024, 6, 17),
    date(2024, 7, 17), date(2024, 8, 15), date(2024, 10, 2),
    date(2024, 11, 15), date(2024, 11, 20), date(2024, 12, 25),
    # 2025 (21-Oct was Muhurat Trading)
    date(2025, 2, 26), date(2025, 3, 14), date(2025, 3, 31),
    date(2025, 4, 10), date(2025, 4, 14), date(2025, 4, 18),
    date(2025, 5, 1), date(2025, 8, 15), date(2025, 8, 27),
    date(2025, 10, 2), date(2025, 10, 22), date(2025, 11, 5),
    date(2025, 12, 25),
    # 2026 current reviewed list
    date(2026, 1, 15), date(2026, 1, 26), date(2026, 3, 3),
    date(2026, 3, 26), date(2026, 3, 31), date(2026, 4, 3),
    date(2026, 4, 14), date(2026, 5, 1), date(2026, 5, 28),
    date(2026, 6, 26), date(2026, 9, 14), date(2026, 10, 2),
    date(2026, 10, 20), date(2026, 11, 10), date(2026, 11, 24),
    date(2026, 12, 25),
}

# These dates are known equity trading sessions even though they are weekends
# or appear as a holiday/Muhurat date in annual holiday circulars.
SPECIAL_TRADING_SESSIONS = {
    date(2020, 11, 14),  # Muhurat, Saturday
    date(2021, 11, 4),   # Muhurat
    date(2022, 10, 24),  # Muhurat
    date(2023, 11, 12),  # Muhurat, Sunday
    date(2024, 1, 20),   # special live/DR session, Saturday
    date(2024, 3, 2),    # special live/DR session, Saturday
    date(2024, 11, 1),   # Muhurat
    date(2025, 2, 1),    # Union Budget, Saturday
    date(2025, 10, 21),  # Muhurat
    date(2026, 2, 1),    # Union Budget, Sunday
    date(2026, 11, 8),   # Muhurat, Sunday
}


def expected_archive_day(day: date) -> bool:
    """True when a completed NSE equity bhavcopy is expected for this date."""
    if day in SPECIAL_TRADING_SESSIONS:
        return True
    return day.weekday() < 5 and day not in FULL_DAY_NONTRADING


def reviewed_full_day_nontrading(day: date) -> bool:
    return day in FULL_DAY_NONTRADING
