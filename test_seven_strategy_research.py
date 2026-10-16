"""Seven NSE swing hypotheses: synthetic regression tests, not market validation."""
import csv
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research_monitor.seven_strategy_rules import STRATEGIES, signals, announcement_flags
from research_monitor.strategy_sources import read_nifty50_tri, read_official_earnings
from swing_backtest import features
from swing_seven_strategy_research import compare, source_inputs


def synthetic_bars(symbol="DEMO", count=1340):
    rng = np.random.default_rng(1420)
    close = 130 * np.exp(np.cumsum(rng.normal(0.0008, 0.016, count)))
    return pd.DataFrame({
        "Symbol": [symbol]*count,
        "Date": pd.date_range("2020-01-01", periods=count),
        "Open": close, "High": close*1.02, "Low": close*.98,
        "Close": close, "Volume": np.full(count, 2_000_000.0),
    })


def meta_write(root, stem, payload, doc_type, **extra):
    (root / (stem + ".csv")).write_bytes(payload)
    (root / (stem + "_metadata.json")).write_text(json.dumps({
        "type": doc_type,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "operator_source_checked": True,
        **extra,
    }), encoding="utf-8")


class SevenStrategyTests(unittest.TestCase):
    def test_exact_seven_frozen_strategies(self):
        self.assertEqual(len(STRATEGIES), 7)
        self.assertEqual(len(set(STRATEGIES)), 7)

    def test_price_signals_only_depend_on_past_and_current_daily_bars(self):
        raw = synthetic_bars()
        old = features(raw.iloc[:800])
        full = features(raw)
        for name in STRATEGIES:
            with self.subTest(name=name):
                past = signals(old, name)
                later = signals(full, name)
                pd.testing.assert_series_equal(
                    past, later.iloc[:800].reset_index(drop=True),
                )
                self.assertEqual(past.dtype, bool)

    def test_unavailable_index_and_earnings_fail_closed(self):
        d = features(synthetic_bars())
        for name in ("relative-strength-v1", "post-earnings-momentum-v1"):
            self.assertEqual(int(signals(d, name).sum()), 0)
        with self.assertRaises(ValueError):
            signals(d, "unreviewed-hyperprofit-v99")

    def test_earnings_announcements_not_tradable_on_release_day(self):
        dates = pd.date_range("2025-01-01", periods=12)
        flags = announcement_flags(dates, [pd.Timestamp("2025-01-03")])
        self.assertFalse(bool(flags.iloc[2]))
        self.assertTrue(flags.iloc[3:8].all())
        self.assertFalse(bool(flags.iloc[8]))

    def test_optional_files_are_private_and_require_digest_and_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            initial = source_inputs(root)
            self.assertIsNone(initial["benchmark"])
            self.assertIsNone(initial["earnings"])

            tri = b"Date,Close\n2024-02-01,22000\n2024-02-02,22021\n"
            meta_write(root, "nifty50_tri_history", tri,
                       "OFFICIAL_NIFTY_50_TRI",
                       index_name="NIFTY 50 TRI",
                       official_source_url="https://www.niftyindices.com/")
            levels, digest = read_nifty50_tri(root)
            self.assertEqual(len(levels), 2)
            self.assertEqual(digest, hashlib.sha256(tri).hexdigest())
            (root/"nifty50_tri_history.csv").write_bytes(tri + b"2024-02-03,22025\n")
            with self.assertRaises(ValueError):
                read_nifty50_tri(root)

    def test_earnings_requires_exact_ist_time_and_original_nse_url(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            row = {
                "Symbol": "DEMO",
                "Announced_At_IST": "2025-02-14T18:05:00+05:30",
                "Revenue_YoY_pct": "14",
                "PAT_YoY_pct": "22",
                "Official_URL": "https://nsearchives.nseindia.com/filing.xml",
                "Document_SHA256": "a"*64,
                "Basis": "CONSOLIDATED",
                "Verified_Primary_Source": "true",
            }
            def store(data):
                buffer = io.StringIO()
                writer = csv.DictWriter(buffer, fieldnames=list(data))
                writer.writeheader()
                writer.writerow(data)
                meta_write(root, "official_earnings_events",
                           buffer.getvalue().encode(),
                           "OFFICIAL_POINT_IN_TIME_NSE_EARNINGS")
            store(row)
            events, _ = read_official_earnings(root)
            self.assertEqual(events["DEMO"], [pd.Timestamp("2025-02-14")])
            for modification in (
                {"Official_URL": "https://unofficial.example/filing.xml"},
                {"Announced_At_IST": "2025-02-14T18:05:00"},
                {"Verified_Primary_Source": "false"},
                {"Basis": "STANDALONE"},
            ):
                with self.subTest(modification=modification):
                    store({**row, **modification})
                    with self.assertRaises(ValueError):
                        read_official_earnings(root)

    def test_price_only_strategies_run_with_no_promotion(self):
        fictitious = {
            "Completed_Trades": 1, "Trades": 2,
            "Mean_Return": 0.50, "Max_Drawdown": 0.05,
        }
        trades = [
            {"Reason": "stop", "Return": -0.03},
            {"Reason": "sample_end", "Return": .99},
        ]
        with patch("swing_seven_strategy_research.simulate",
                   return_value=(fictitious, trades)) as sim:
            result = compare(synthetic_bars(), "SYNTHETIC_ONLY", 1)
        self.assertEqual(sim.call_count, 10)  # five eligible strategies, two costs
        self.assertEqual(len(result["summary"]), 5)
        self.assertEqual(
            result["strategy_statuses"]["relative-strength-v1"],
            "NIFTY_50_TRI_SOURCE_REQUIRED",
        )
        self.assertEqual(
            result["strategy_statuses"]["post-earnings-momentum-v1"],
            "DATED_OFFICIAL_EARNINGS_REQUIRED",
        )
        self.assertEqual(result["research_top_three_unvalidated"], [])
        self.assertEqual(result["verified_top_three"], [])
        self.assertEqual(result["verified_profitable_strategies"], 0)
        self.assertFalse(result["live_eligible"])
        self.assertEqual(result["orders"], "DISABLED")
        self.assertEqual(
            result["summary"]["trend-following-v1"]["pooled_completed_mean_net_return"],
            -0.03,
        )

    def test_post_design_data_cannot_change_frozen_sample(self):
        raw = synthetic_bars()
        # Any bars after frozen design cutoff are dropped before simulation.
        last = raw.iloc[-1:].copy()
        last["Date"] = pd.Timestamp("2026-10-17")
        modified = pd.concat([raw, last], ignore_index=True)
        stats = {"Completed_Trades": 0, "Trades": 0,
                 "Mean_Return": 0., "Max_Drawdown": 0.}
        with patch("swing_seven_strategy_research.simulate",return_value=(stats,[])):
            a = compare(raw, "synthetic", 1)
            b = compare(modified, "synthetic", 1)
        self.assertEqual(a["summary"], b["summary"])
        self.assertEqual(a["stock_observations"], b["stock_observations"])


if __name__ == "__main__":
    unittest.main()
