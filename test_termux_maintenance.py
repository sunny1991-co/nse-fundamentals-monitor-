import csv
import tempfile
import unittest
from pathlib import Path

from termux_maintenance import summary


class TermuxMaintenanceTests(unittest.TestCase):
    def write_csv(self, path, rows):
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_summary_distinguishes_verified_evidence_from_missing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_csv(root/'annual_fundamental_quality_summary.csv', [
                {'Financial_Year':'2024-25','Rows_Assessed':'1','Unique_Symbols':'1',
                 'Six_Core_Fields_Complete':'1','NSE_Metadata_XBRL_Basis_Match':'1',
                 'Year_Data_Quality_Pass':'1','Rows_Requiring_Data_Review':'0'},
                {'Financial_Year':'FUNDAMENTAL_SCREEN','Rows_Assessed':'1','Unique_Symbols':'1',
                 'Six_Core_Fields_Complete':'1','NSE_Metadata_XBRL_Basis_Match':'0',
                 'Year_Data_Quality_Pass':'0','Rows_Requiring_Data_Review':'1'},
            ])
            self.write_csv(root/'final_fundamental_screen_summary.csv', [
                {'Final_Shortlist_Count':'0'}
            ])
            self.write_csv(root/'quarterly_collection_status.csv', [
                {'Collection_Status':'OK','Verified_Quarter_Rows':'12','Qualified_Comparison_Rows':'0'}
            ])
            self.write_csv(root/'nse_sector_map.csv', [
                {'Lookup_Status':'OK'}, {'Lookup_Status':'ERROR'}
            ])
            result = summary(root)
            self.assertEqual(result['two_year_baseline_pass'], 1)
            self.assertEqual(result['annual_full_pass'], 0)
            self.assertEqual(result['quarterly_verified_rows'], 12)
            self.assertEqual(result['sector_verified_rows'], 1)
            self.assertEqual(result['adjusted_history'], 'MISSING')
            self.assertEqual(result['official_eod_history_state'], 'NOT_STARTED')
            self.assertEqual(result['official_eod_actions'], 'NOT_STARTED')
            self.assertEqual(result['upstox'], 'INTENTIONALLY_DEFERRED')
            self.assertEqual(result['orders'], 'MANUAL_ONLY')


if __name__ == '__main__':
    unittest.main()
