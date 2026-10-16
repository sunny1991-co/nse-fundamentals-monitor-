import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
import pandas as pd
import nse_quarterly_monitor as q
import final_fundamental_shortlist as final
from research_monitor.filing_dates import parse_filing_date


class FilingDateTests(unittest.TestCase):
    def test_iso_dates_never_swap_month_and_day(self):
        for text in ('2025-04-01','2025-07-01','2025-10-01','2026-01-01'):
            self.assertEqual(q.parse_date(text),date.fromisoformat(text))
        self.assertEqual(parse_filing_date('2025-04-01T15:30:00+05:30'),date(2025,4,1))

    def test_invalid_iso_never_reinterpreted(self):
        for text in ('2026-13-01','2025-02-29','2025-00-12','2025-04-31',None,'nan',''):
            self.assertIsNone(q.parse_date(text))

    def test_explicit_indian_formats_supported(self):
        for text in ('01-Apr-2025','01-April-2025','01-04-2025','01/04/2025','01-Apr-2025 14:30:00'):
            self.assertEqual(q.parse_date(text),date(2025,4,1))

    def test_all_four_xml_quarter_contexts_parse(self):
        for start,end in [('2025-04-01','2025-06-30'),('2025-07-01','2025-09-30'),('2025-10-01','2025-12-31'),('2026-01-01','2026-03-31')]:
            body=f'''<xbrl><context id="Q"><period><startDate>{start}</startDate><endDate>{end}</endDate></period></context><NatureOfReportStandaloneConsolidated contextRef="Q">Consolidated</NatureOfReportStandaloneConsolidated><RevenueFromOperations contextRef="Q">10000000</RevenueFromOperations></xbrl>'''.encode()
            row,reason=q.parse_xbrl(body,dict(symbol='TEST',qe_Date=end,consolidated='Consolidated'),'https://nsearchives.nseindia.com/corporate/xbrl/TEST_WEB.xml')
            with self.subTest(start=start):
                self.assertEqual(reason,'')
                self.assertEqual(row['Quarter_End'],end)
                self.assertEqual(row['Revenue_Operations_Cr'],1)
                self.assertEqual(row['Parser_Version'],q.PARSER_VERSION)
                self.assertTrue(89<=row['Quarter_Context_Days']<=92)


class QuarterComparisonTests(unittest.TestCase):
    def frame(self,dates):
        return pd.DataFrame([dict(Symbol='TEST',Quarter_End=day,Filing_Published_At=day,Parser_Version=q.PARSER_VERSION,**{key:(i+1)*10 for key in q.VALUE_FIELDS}) for i,day in enumerate(dates)])

    def test_yoy_uses_matching_date_despite_missing_intermediate_quarters(self):
        report=q.build_change_report(self.frame(['2024-06-30','2025-06-30']))
        self.assertEqual(report.iloc[-1].Revenue_YoY_Change_Pct,100)
        self.assertTrue(pd.isna(report.iloc[-1].Revenue_QoQ_Change_Pct))

    def test_all_quarter_end_boundaries(self):
        report=q.build_change_report(self.frame(['2024-12-31','2025-03-31','2025-06-30','2025-09-30','2025-12-31']))
        self.assertTrue(report.iloc[1:].Revenue_QoQ_Change_Pct.notna().all())
        self.assertEqual(report.iloc[-1].Revenue_YoY_Change_Pct,400)

    def test_old_parser_and_zero_baseline_never_supply_growth(self):
        frame=self.frame(['2024-06-30','2025-06-30'])
        frame.loc[0,'Parser_Version']='5'
        report=q.build_change_report(frame)
        self.assertEqual(len(report),1)
        self.assertTrue(pd.isna(report.iloc[-1].Revenue_YoY_Change_Pct))
        frame.loc[0,'Parser_Version']=q.PARSER_VERSION
        frame.loc[0,'Revenue_Operations_Cr']=0
        self.assertTrue(pd.isna(q.build_change_report(frame).iloc[-1].Revenue_YoY_Change_Pct))

    def test_legacy_file_without_version_is_excluded(self):
        self.assertTrue(q.build_change_report(self.frame(['2025-06-30']).drop(columns='Parser_Version')).empty)


class ParserMigrationTests(unittest.TestCase):
    def test_old_raw_records_preserved_but_not_qualified(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            paths=dict(ANNUAL_QUALITY_FILE=root/'annual.csv',RESULTS_FILE=root/'raw.csv',COMPARE_FILE=root/'changes.csv',UNVERIFIED_FILE=root/'unverified.csv',PROCESSED_FILE=root/'processed.csv',COLLECTION_STATUS_FILE=root/'status.csv')
            pd.DataFrame([dict(Symbol='TEST',Full_Fundamental_Criteria_Pass=True)]).to_csv(paths['ANNUAL_QUALITY_FILE'],index=False)
            row=dict(Symbol='TEST',Quarter_End='2026-06-30',Filing_Published_At='2026-07-15',Source_URL='https://nsearchives.nseindia.com/corporate/xbrl/TEST.xml',Fundamentals_Complete=True,Parser_Version='5',**{key:10 for key in q.VALUE_FIELDS})
            pd.DataFrame([row]).to_csv(paths['RESULTS_FILE'],index=False)
            with patch.multiple(q,**paths),patch.object(q,'make_session',return_value=Mock()),patch.object(q,'fetch_recent_records',return_value=[]):
                q.main()
            raw=pd.read_csv(paths['RESULTS_FILE'],dtype=str)
            self.assertEqual(len(raw),1)
            self.assertEqual(raw.iloc[0].Parser_Version,'5')
            self.assertTrue(pd.read_csv(paths['COMPARE_FILE']).empty)
            status=pd.read_csv(paths['COLLECTION_STATUS_FILE']).iloc[0]
            self.assertEqual(status.Verified_Quarter_Rows,0)
            self.assertEqual(status.Reverification_Required_Rows,1)

    def test_final_source_gate_rejects_old_or_absent_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'status.csv'
            for version in ('','5',q.PARSER_VERSION):
                pd.DataFrame([dict(Run_Date=date.today().isoformat(),Collection_Status='OK',Parser_Version=version)]).to_csv(path,index=False)
                ready,reason=final.quarterly_source_readiness(path)
                self.assertEqual(ready,version==q.PARSER_VERSION)
                if not ready:self.assertEqual(reason,'QUARTERLY_PARSER_REVERIFICATION_REQUIRED')


if __name__=='__main__':unittest.main()
