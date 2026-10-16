import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import swing_technical_screener as screener


class TechnicalReuseTests(unittest.TestCase):
    def test_matching_internal_backtest_can_be_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = pd.DataFrame({'Symbol': ['AAA', 'AAA', 'BBB']})
            pd.DataFrame([
                {
                    'Symbol': 'AAA',
                    'Backtest_Pass': 'True',
                    'Reason': 'TECHNICAL_HOLDOUT_PASS_NOT_CALIBRATED_PROBABILITY',
                    'Data_SHA256': 'digest',
                    'Strategy': screener.VERSION,
                },
                {
                    'Symbol': 'BBB',
                    'Backtest_Pass': 'False',
                    'Reason': 'LESS_THAN_1260_BARS',
                    'Data_SHA256': '',
                    'Strategy': '',
                },
            ]).to_csv(root/'swing_backtest_report.csv', index=False)
            with patch.object(screener, 'ROOT', root):
                self.assertTrue(screener.current_backtest_matches(raw, 'digest'))

    def test_stale_digest_or_missing_symbol_forces_recompute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = pd.DataFrame({'Symbol': ['AAA', 'BBB']})
            pd.DataFrame([
                {
                    'Symbol': 'AAA',
                    'Backtest_Pass': 'False',
                    'Reason': 'INSUFFICIENT_OR_WEAK_EVIDENCE',
                    'Data_SHA256': 'old',
                    'Strategy': screener.VERSION,
                },
            ]).to_csv(root/'swing_backtest_report.csv', index=False)
            with patch.object(screener, 'ROOT', root):
                self.assertFalse(screener.current_backtest_matches(raw, 'digest'))


if __name__ == '__main__':
    unittest.main()
