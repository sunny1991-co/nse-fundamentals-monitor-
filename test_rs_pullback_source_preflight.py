"""Evidence-gated local source diagnostics; no prices are invented or promoted."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
import unittest

import numpy as np
import pandas as pd

from swing_rs_pullback_research import (
    source_preflight, HISTORY_INPUTS, TRI_INPUTS, FROZEN_SAMPLE_END,
)


def make_history():
    days=pd.bdate_range(end="2026-10-15",periods=1270)
    return pd.DataFrame({
        "Symbol":["SYNTH"]*len(days), "Date":days,
        "Close":np.full(len(days),100.0),
    })


def make_benchmark(count=1300):
    dates=pd.bdate_range(end="2026-10-15",periods=count)
    return pd.Series(np.arange(count,dtype=float)+100., index=dates)


class SwingResearchPreflightTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)

    def touch(self, names):
        for name in names:
            (self.root/name).write_bytes(b"SYNTHETIC_TEST_ONLY")

    def test_missing_equity_history_is_distinguished_from_tri(self):
        hist=Mock(side_effect=AssertionError("must not read missing data"))
        tri=Mock(side_effect=AssertionError("must not read missing data"))
        value,status=source_preflight(self.root,history_reader=hist,tri_reader=tri)
        self.assertIsNone(value)
        self.assertEqual(status["reason_code"],"HISTORY_FILES_MISSING")
        self.assertEqual(status["missing_files"],list(HISTORY_INPUTS))
        hist.assert_not_called()
        tri.assert_not_called()

    def test_missing_tri_reported_after_valid_stock_history(self):
        self.touch(HISTORY_INPUTS)
        hist=Mock(return_value=(make_history(),"synthetic_stock_hash"))
        tri=Mock(side_effect=AssertionError("must not read absent TRI"))
        value,status=source_preflight(self.root,history_reader=hist,tri_reader=tri)
        self.assertIsNone(value)
        self.assertEqual(status["blocked_stage"],"OFFICIAL_NIFTY_50_TOTAL_RETURN_INDEX")
        self.assertEqual(status["reason_code"],"TRI_FILES_MISSING")
        self.assertEqual(status["missing_files"],list(TRI_INPUTS))
        hist.assert_called_once()
        tri.assert_not_called()

    def test_bad_tri_attestation_never_accepted(self):
        self.touch(HISTORY_INPUTS+TRI_INPUTS)
        value,status=source_preflight(
            self.root,
            history_reader=lambda _: (make_history(),"stock"),
            tri_reader=Mock(side_effect=ValueError("UNVERIFIED_SOURCE_METADATA")),
        )
        self.assertIsNone(value)
        self.assertEqual(status["reason_code"],"UNVERIFIED_SOURCE_METADATA")

    def test_missing_tri_260_history_blocks(self):
        self.touch(HISTORY_INPUTS+TRI_INPUTS)
        value,status=source_preflight(
            self.root,
            history_reader=lambda _: (make_history(),"stock"),
            tri_reader=lambda _: (make_benchmark(259),"benchmark"),
        )
        self.assertIsNone(value)
        self.assertEqual(status["reason_code"],"FROZEN_TRI_WARMUP_INSUFFICIENT")
        self.assertEqual(status["minimum_tri_rows"],260)

    def test_older_inspected_sample_cannot_use_future_tri(self):
        self.touch(HISTORY_INPUTS+TRI_INPUTS)
        tri=make_benchmark(260)
        tri.index=pd.bdate_range(start="2026-10-19",periods=260)
        value,status=source_preflight(
            self.root,
            history_reader=lambda _: (make_history(),"stock"),
            tri_reader=lambda _: (tri,"benchmark"),
        )
        self.assertIsNone(value)
        self.assertEqual(status["reason_code"],"FROZEN_TRI_WARMUP_INSUFFICIENT")

    def test_short_stock_history_is_not_an_eligible_research_universe(self):
        self.touch(HISTORY_INPUTS+TRI_INPUTS)
        bars=make_history().tail(350).reset_index(drop=True)
        value,status=source_preflight(
            self.root,
            history_reader=lambda _: (bars,"stock"),
            tri_reader=lambda _: (make_benchmark(),"benchmark"),
        )
        self.assertIsNone(value)
        self.assertEqual(status["reason_code"],"NO_STOCK_WITH_1260_FROZEN_HISTORICAL_BARS")

    def test_ready_reports_are_research_only_and_do_not_write_orders(self):
        self.touch(HISTORY_INPUTS+TRI_INPUTS)
        values,status=source_preflight(
            self.root,
            history_reader=lambda _: (make_history(),"stock"),
            tri_reader=lambda _: (make_benchmark(),"benchmark"),
        )
        self.assertIsNotNone(values)
        self.assertEqual(status["state"],"SOURCE_PREFLIGHT_PASSED_RESEARCH_ONLY")
        self.assertEqual(status["long_history_stocks"],1)
        self.assertFalse(status["historical_source_attestation_is_independent_audit"])
        self.assertEqual(status["trading_orders"],"DISABLED")
        self.assertFalse((self.root/"runtime").exists())

    def test_no_private_exception_text_is_ever_reflected(self):
        self.touch(HISTORY_INPUTS+TRI_INPUTS)
        secret="Bearer NEVER_INCLUDE_IN_REPORT"
        value,status=source_preflight(
            self.root,
            history_reader=Mock(side_effect=ValueError(secret)),
            tri_reader=Mock(),
        )
        self.assertIsNone(value)
        self.assertEqual(status["reason_code"],"HISTORY_INVALID_OR_UNREADABLE")
        self.assertNotIn(secret,str(status))


if __name__ == "__main__":
    unittest.main()
