import tempfile
import unittest
from pathlib import Path

from termux_bootstrap_data import seed


class TermuxBootstrapTests(unittest.TestCase):
    def test_seed_copies_csv_once_and_never_overwrites_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            code=base/'repo'; data=base/'data'
            code.mkdir()
            (code/'a.csv').write_text('public-v1')
            result=seed(code,data)
            self.assertEqual((data/'a.csv').read_text(),'public-v1')
            self.assertIn('a.csv',result['copied'])
            (data/'a.csv').write_text('private-newer')
            (code/'a.csv').write_text('public-v2')
            result=seed(code,data)
            self.assertEqual((data/'a.csv').read_text(),'private-newer')
            self.assertIn('a.csv',result['skipped_existing'])
            self.assertTrue((data/'runtime').is_dir())

    def test_seed_refuses_repo_or_nested_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            code=Path(directory)/'repo';code.mkdir()
            for target in (code, code/'data'):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    seed(code,target)


if __name__=='__main__':
    unittest.main()
