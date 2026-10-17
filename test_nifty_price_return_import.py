"""Synthetic integrity tests for the isolated price return importer."""
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from nifty_price_return_import import import_dir, parse_csv, sha


class PriceReturnImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root/'download'
        self.src.mkdir()
        self.dst = self.root/'private'

    def make(self, name, day, *, index='NIFTY 50', close='101', high='102'):
        p = self.src/name
        p.write_text('Index Name,Date,Open,High,Low,Close\n'
                     f'{index},{day},100,{high},99,{close}\n')
        return p

    def test_valid_two_yearly_files_research_only(self):
        self.make('NIFTY 50_Historical_PR_A.csv', '20 Oct 2020')
        self.make('NIFTY 50_Historical_PR_B.csv', '21 Oct 2020')
        result = import_dir(self.src, self.dst, today=date(2026, 10, 17))
        self.assertEqual(result['rows'], 2)
        m = json.loads((self.dst/'nifty50_pr_history_metadata.json').read_text())
        self.assertEqual(m['return_kind'], 'PRICE_RETURN_NOT_TRI')
        self.assertFalse(m['operator_source_checked'])
        self.assertFalse(m['eligible_for_tri_strategy_benchmark'])
        self.assertEqual(m['live_orders'], 'DISABLED')
        self.assertEqual(m['sha256'], sha((self.dst/'nifty50_pr_history.csv').read_bytes()))
        self.assertIn('2020-10-20', (self.dst/'nifty50_pr_history.csv').read_text())

    def test_gross_tri_schema_rejected(self):
        body = b'Index Name,Date,Total Returns Index\nNIFTY 50,20 Oct 2020,14000\n'
        with self.assertRaisesRegex(ValueError, 'PR_OHLC_HEADER_REQUIRED_NOT_TRI'):
            parse_csv(body, 'NIFTY 50', today=date(2026, 10, 17))

    def test_wrong_index_rejected(self):
        self.make('NIFTY 50_Historical_PR_A.csv', '20 Oct 2020', index='NIFTY 100')
        with self.assertRaisesRegex(ValueError, 'PR_WRONG_INDEX'):
            import_dir(self.src, self.dst, today=date(2026, 10, 17))

    def test_price_conflict_rejected(self):
        self.make('NIFTY 50_Historical_PR_A.csv', '20 Oct 2020')
        self.make('NIFTY 50_Historical_PR_B.csv', '20 Oct 2020', close='102')
        with self.assertRaisesRegex(ValueError, 'PR_OVERLAPPING_VALUES_CONFLICT'):
            import_dir(self.src, self.dst, today=date(2026, 10, 17))
        self.assertFalse(self.dst.exists())

    def test_invalid_ohlc_rejected(self):
        self.make('NIFTY 50_Historical_PR_A.csv', '20 Oct 2020', high='99')
        with self.assertRaisesRegex(ValueError, 'PR_OHLC_INCONSISTENT'):
            import_dir(self.src, self.dst, today=date(2026, 10, 17))

    def test_protect_prior_metadata(self):
        self.make('NIFTY 50_Historical_PR_A.csv', '20 Oct 2020')
        import_dir(self.src, self.dst, today=date(2026, 10, 17))
        with self.assertRaisesRegex(ValueError, 'PR_RESEARCH_OUTPUT_EXISTS'):
            import_dir(self.src, self.dst, today=date(2026, 10, 17))
        (self.dst/'nifty50_pr_history_metadata.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'PR_EXISTING_RESEARCH_INTEGRITY_INVALID'):
            import_dir(self.src, self.dst, today=date(2026, 10, 17), replace_research_only=True)

    def test_independent_nifty100(self):
        self.make('NIFTY 100_Historical_PR_A.csv', '20 Oct 2020', index='NIFTY 100')
        result = import_dir(self.src, self.dst, 'NIFTY 100', today=date(2026, 10, 17))
        self.assertEqual(result['index_name'], 'NIFTY 100')
        self.assertTrue((self.dst/'nifty100_pr_history.csv').exists())


if __name__ == '__main__':
    unittest.main()
