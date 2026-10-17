"""Synthetic source-verification fixtures; no production facts fabricated."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import requests
from research_monitor.legacy_xbrl import verify
from nse_legacy_xbrl_verify import collect, download, candidate_priority

URL='https://nsearchives.nseindia.com/corporate/xbrl/TEST.xml'
RECORD=dict(symbol='TEST',qe_Date='2023-03-31',consolidated='Consolidated',audited='Audited',broadcast_Date='2023-05-01')


def fixture():
    return b'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:f="urn:synthetic-financial-facts">
    <xbrli:context id="Y"><xbrli:entity><xbrli:identifier scheme="http://www.nseindia.com/NSESymbol">TEST</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2022-04-01</xbrli:startDate><xbrli:endDate>2023-03-31</xbrli:endDate></xbrli:period></xbrli:context>
    <xbrli:context id="I"><xbrli:entity><xbrli:identifier scheme="http://www.nseindia.com/NSESymbol">TEST</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2023-03-31</xbrli:instant></xbrli:period></xbrli:context>
    <xbrli:unit id="R"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>
    <xbrli:unit id="E"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
    <f:Symbol contextRef="Y">TEST</f:Symbol>
    <f:NatureOfReportStandaloneConsolidated contextRef="Y">Consolidated</f:NatureOfReportStandaloneConsolidated>
    <f:WhetherResultsAreAuditedOrUnaudited contextRef="Y">Audited</f:WhetherResultsAreAuditedOrUnaudited>
    <f:RevenueFromOperations contextRef="Y" unitRef="R" decimals="-5">100000000</f:RevenueFromOperations>
    <f:ProfitLossForPeriod contextRef="Y" unitRef="R">10000000</f:ProfitLossForPeriod>
    <f:BasicEarningsLossPerShare contextRef="Y" unitRef="E">2</f:BasicEarningsLossPerShare>
    <f:Assets contextRef="I" unitRef="R">200000000</f:Assets>
    <f:Equity contextRef="I" unitRef="R">100000000</f:Equity>
    <f:Borrowings contextRef="I" unitRef="R">10000000</f:Borrowings>
    </xbrli:xbrl>'''


class LegacyParserTests(unittest.TestCase):
    def test_correct_source_and_units_not_rounding_multiplier(self):
        row,reason=verify(RECORD,URL,fixture())
        self.assertEqual(reason,'')
        self.assertEqual(row['Revenue_Operations_Cr'],10)
        self.assertEqual(row['Basic_EPS_Rs'],2)
        self.assertTrue(row['Six_Core_Fields_Complete'])
        self.assertEqual(len(row['Source_SHA256']),64)

    def test_quarter_not_annual_even_when_metadata_says_annual(self):
        row,reason=verify(RECORD,URL,fixture().replace(b'2022-04-01',b'2023-01-01'))
        self.assertIsNone(row)
        self.assertEqual(reason,'EXACT_APRIL_MARCH_CONTEXT_NOT_VERIFIED')

    def test_symbol_and_entity_must_match(self):
        for body in (fixture().replace(b'>TEST</f:Symbol>',b'>OTHER</f:Symbol>'),
                     fixture().replace(b'>TEST</xbrli:identifier>',b'>OTHER</xbrli:identifier>')):
            with self.subTest(body=body):self.assertIsNone(verify(RECORD,URL,body)[0])

    def test_rejects_bad_units_nan_conflicts_and_unaudited(self):
        changes=[(b'iso4217:INR',b'iso4217:USD'),
                 (b'>100000000</f:RevenueFromOperations>',b'>NaN</f:RevenueFromOperations>'),
                 (b'>100000000</f:RevenueFromOperations>',b'>1e999</f:RevenueFromOperations>'),
                 (b'>Audited<',b'>Unaudited<'),
                 (b'>Consolidated<',b'>Standalone<'),
                 (b'decimals="-5"',b'decimals="-5" scale="5"'),
                 (b'</xbrli:xbrl>',b'<f:RevenueFromOperations contextRef="Y" unitRef="R">50</f:RevenueFromOperations></xbrli:xbrl>')]
        for old,new in changes:
            with self.subTest(change=new):self.assertIsNone(verify(RECORD,URL,fixture().replace(old,new))[0])

    def test_reporting_period_conflict_is_not_repaired(self):
        body=fixture().replace(b'</xbrli:xbrl>',b'<f:DateOfStartOfReportingPeriod contextRef="Y">2023-01-01</f:DateOfStartOfReportingPeriod></xbrli:xbrl>')
        self.assertEqual(verify(RECORD,URL,body)[1],'CONTEXT_AND_REPORTING_PERIOD_FACT_CONFLICT')

    def test_missing_fact_is_not_zero(self):
        body=fixture().replace(b'<f:Borrowings contextRef="I" unitRef="R">10000000</f:Borrowings>',b'')
        row,_=verify(RECORD,URL,body)
        self.assertIsNone(row['Total_Borrowings_Cr'])
        self.assertFalse(row['Six_Core_Fields_Complete'])

    def test_unofficial_url_and_dtd_rejected(self):
        self.assertIsNone(verify(RECORD,'https://example.com/a.xml',fixture())[0])
        self.assertIsNone(verify(RECORD,URL,b'<!DOCTYPE test>'+fixture())[0])

    def test_duplicate_context_ids_rejected(self):
        body=fixture().replace(b'id="I"',b'id="Y"')
        self.assertEqual(verify(RECORD,URL,body)[1],'MISSING_OR_DUPLICATE_CONTEXT_UNIT_ID')


class LegacyCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'FY2425.csv').write_text('Symbol,Company\nTEST,Synthetic Test\n')
        self.index('TEST',URL)

    def index(self,symbol,url):
        fields=['Symbol','Company Name','Period Ended','Audited / Unaudited','Consolidated / Non-Consolidated','Period','XBRL URL','Broadcast Date']
        path=self.root/'nse_legacy_financial_results_2022-23.csv'
        with path.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
            w.writerow(dict(zip(fields,[symbol,'Synthetic Test','31-Mar-2023','Audited','Consolidated','Annual',url,'01-May-2023 15:00:00'])))

    def test_verified_history_appended_and_restart_skips(self):
        fetch=Mock(return_value=fixture())
        result=collect(self.root,session=Mock(),fetch=fetch,pause=lambda _:None)
        self.assertEqual(result['Added_Verified'],1)
        before=(self.root/'annual_historical_results.csv').read_bytes()
        collect(self.root,session=Mock(),fetch=fetch,pause=lambda _:None)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual((self.root/'annual_historical_results.csv').read_bytes(),before)

    def test_403_stops_and_preserves_existing_history(self):
        path=self.root/'annual_historical_results.csv'
        path.write_text('Symbol,Financial_Year,Fiscal_Year_End\nOLD,2024-25,2025-03-31\n');before=path.read_bytes()
        fetch=Mock(side_effect=requests.HTTPError('denied',response=Mock(status_code=403)))
        result=collect(self.root,session=Mock(),fetch=fetch,pause=lambda _:None)
        self.assertEqual(result['Status'],'STOPPED_ON_403_429')
        self.assertEqual(result['Added_Verified'],0)
        self.assertEqual(path.read_bytes(),before)

    def test_inconsistent_xml_stays_in_ledger_only(self):
        collect(self.root,session=Mock(),fetch=lambda *_:fixture().replace(b'2022-04-01',b'2023-01-01'),pause=lambda _:None)
        self.assertFalse((self.root/'annual_historical_results.csv').exists())
        self.assertIn('EXACT_APRIL_MARCH_CONTEXT_NOT_VERIFIED',(self.root/'legacy_xbrl_verification.csv').read_text())

    def test_enforced_preflight_quarantines_recent_403_and_q4_xml(self):
        # The preflight's immutable source-ready facts, not index labels,
        # must determine which URLs an unattended verifier can download.
        from nse_legacy_xbrl_preflight import OUTPUT as READY
        ready=self.root/READY
        ready.write_text(
            'Symbol,Financial_Year,Official_XBRL_Source_URL,'
            'Source_Review_State,Metadata_Ready_For_XML,'
            'Independent_Audited_Consolidated_Annual_XML_Checked_This_Run\n'
            'TEST,2022-23,'+URL+',PREVIOUS_SOURCE_ATTEMPT_COOLDOWN,false,false\n'
        )
        fetch=Mock(return_value=fixture())
        with patch('nse_legacy_xbrl_preflight.snapshot') as snapshot:
            result=collect(self.root,session=Mock(),fetch=fetch,
                           require_preflight=True,pause=lambda _:None)
        snapshot.assert_called_once()
        self.assertEqual(result['Original_Index_Candidates_Before_Preflight'],1)
        self.assertEqual(result['Excluded_By_Current_Source_Preflight'],1)
        self.assertEqual(result['Candidate_Symbol_Years'],0)
        self.assertEqual(result['Attempted'],0)
        self.assertEqual(result['Added_Verified'],0)
        self.assertEqual(fetch.call_count,0)
        self.assertFalse((self.root/'annual_historical_results.csv').exists())

    def test_enforced_preflight_allows_exact_safe_old_year_url_only(self):
        from nse_legacy_xbrl_preflight import OUTPUT as READY
        (self.root/READY).write_text(
            'Symbol,Financial_Year,Official_XBRL_Source_URL,'
            'Source_Review_State,Metadata_Ready_For_XML,'
            'Independent_Audited_Consolidated_Annual_XML_Checked_This_Run\n'
            'TEST,2022-23,'+URL+',READY_FOR_INDEPENDENT_ORIGINAL_XML_CONTEXT_REVIEW,true,false\n'
        )
        fetch=Mock(return_value=fixture())
        with patch('nse_legacy_xbrl_preflight.snapshot'):
            result=collect(self.root,session=Mock(),fetch=fetch,
                           require_preflight=True,pause=lambda _:None)
        self.assertTrue(result['Independent_Source_Preflight_Required'])
        self.assertEqual(result['Candidate_Symbol_Years'],1)
        self.assertEqual(result['Attempted'],1)
        self.assertEqual(result['Added_Verified'],1)
        self.assertEqual(fetch.call_count,1)
        self.assertIn('TEST',(self.root/'annual_historical_results.csv').read_text())

    def test_missing_official_preflight_never_falls_back_to_unsafe_download(self):
        fetch=Mock(return_value=fixture())
        with patch('nse_legacy_xbrl_preflight.snapshot',
                   side_effect=ValueError('CURRENT_NSE_EQ_CATALOG_UNATTESTED')):
            with self.assertRaisesRegex(ValueError,'UNATTESTED'):
                collect(self.root,session=Mock(),fetch=fetch,
                        require_preflight=True,pause=lambda _:None)
        self.assertEqual(fetch.call_count,0)
        self.assertFalse((self.root/'annual_historical_results.csv').exists())

    def test_prioritize_nearly_complete_four_year_research_first(self):
        # Ranking never promotes facts; it chooses which independently
        # indexed and source-safe old XML can be inspected first.
        other={('HIGH','2022-23')}
        present={'HIGH':2,'MID':2,'ONE':1}
        complete={'HIGH':2,'MID':2}
        self.assertEqual(candidate_priority('HIGH','2023-24',
                                            present,complete,other),0)
        self.assertEqual(candidate_priority('MID','2022-23',
                                            present,complete,other),1)
        self.assertEqual(candidate_priority('FILL','2023-24',
                                            {'FILL':2},{},set()),3)
        self.assertEqual(candidate_priority('ONE','2022-23',
                                            present,complete,set()),4)
        self.assertEqual(candidate_priority('NONE','2022-23',
                                            present,complete,set()),5)

    def test_plan_only_cannot_create_an_http_session_or_write_ledger(self):
        from nse_legacy_xbrl_preflight import OUTPUT as READY
        ready=self.root/READY
        ready.write_text(
            'Symbol,Financial_Year,Official_XBRL_Source_URL,'
            'Source_Review_State,Metadata_Ready_For_XML,'
            'Independent_Audited_Consolidated_Annual_XML_Checked_This_Run\n'
            'TEST,2022-23,'+URL+','
            'READY_FOR_INDEPENDENT_ORIGINAL_XML_CONTEXT_REVIEW,true,false\n'
        )
        with (
            patch('nse_legacy_xbrl_preflight.snapshot') as preflight,
            patch('nse_legacy_xbrl_verify.requests.Session',
                  side_effect=AssertionError('HTTP SESSION NEVER ALLOWED')),
            patch('nse_legacy_xbrl_verify.baseline_coverage',
                  return_value=({'TEST':2},{'TEST':2})),
        ):
            planned=collect(self.root,limit=10,require_preflight=True,
                            plan_only=True)
        preflight.assert_called_once()
        self.assertEqual(planned['Status'],
                         'SAFE_PREFLIGHT_RESEARCH_PLAN_ONLY_NO_HTTP')
        self.assertEqual(planned['Freshly_Preflighted_Ready_Candidates'],1)
        self.assertEqual(planned['XML_Network_Requests'],0)
        self.assertEqual(planned['Original_XBRL_Facts_Verified'],0)
        self.assertEqual(planned['Strict_Fundamental_Passes_Added'],0)
        self.assertEqual(planned['Orders'],'DISABLED')
        self.assertEqual(planned['Proposed_Next_Source_Symbol_Years'],[
            {'Symbol':'TEST','Financial_Year':'2022-23',
             'Research_Priority':1,
             'Existing_Other_Legacy_Year_Verified_In_History':False}
        ])
        self.assertFalse((self.root/'legacy_xbrl_verification.csv').exists())
        self.assertFalse((self.root/'legacy_xbrl_run_status.csv').exists())
        self.assertFalse((self.root/'annual_historical_results.csv').exists())

    def test_plan_only_refuses_missing_independent_preflight(self):
        with self.assertRaisesRegex(ValueError,
                                    'OFFLINE_PLAN_REQUIRES_FRESH_OFFICIAL'):
            collect(self.root,limit=1,plan_only=True,require_preflight=False)

    def test_plan_only_preserves_previous_verification_attempt_bytes(self):
        original=b'Source_URL,Status,Reason\n'+URL.encode()+b',ACCESS_BLOCKED,SOURCE_HTTP_403\n'
        file=self.root/'legacy_xbrl_verification.csv'
        file.write_bytes(original)
        from nse_legacy_xbrl_preflight import OUTPUT as READY
        (self.root/READY).write_text(
            'Symbol,Financial_Year,Official_XBRL_Source_URL,'
            'Source_Review_State,Metadata_Ready_For_XML,'
            'Independent_Audited_Consolidated_Annual_XML_Checked_This_Run\n'
            'TEST,2022-23,'+URL+',PREVIOUS_SOURCE_ATTEMPT_COOLDOWN,false,false\n'
        )
        with patch('nse_legacy_xbrl_preflight.snapshot'):
            planned=collect(self.root,require_preflight=True,plan_only=True)
        self.assertEqual(planned['Freshly_Preflighted_Ready_Candidates'],0)
        self.assertEqual(planned['Excluded_By_Current_Source_Preflight'],1)
        self.assertEqual(file.read_bytes(),original)
        self.assertFalse((self.root/'annual_historical_results.csv').exists())

    def test_redirect_not_followed(self):
        response=Mock(status_code=302)
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        session=Mock();session.get.return_value=response
        with self.assertRaises(requests.HTTPError):download(session,URL)
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])


if __name__ == '__main__':unittest.main()
