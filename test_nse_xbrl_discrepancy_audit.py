"""Synthetic raw original-XBRL unit/context and bounded downloader audit tests."""
from __future__ import annotations

from datetime import datetime
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from nse_xbrl_discrepancy_audit import (
    inspect_xml, _download, drilldown, DrilldownError,
    MAX_FILES_PER_RUN, SCOPE,
)
from upstox_reference_batches import _stored_paths
from test_upstox_reference_reconcile import (
    BASE, original_row, SYMBOL,
)


def valid_xml(*, symbol=SYMBOL, another_context=False,
              currency="iso4217:INR", basis="Consolidated",
              duplicate=False, revenue=1000_000_000,
              profit=250_000_000, cash=200_000_000):
    nodes=[
        '<root xmlns:xbrli="http://www.xbrl.org/2003/instance" '
        'xmlns:xbrldi="http://xbrl.org/2006/xbrldi">',
        '<xbrli:unit id="INR"><xbrli:measure>'+currency+
        '</xbrli:measure></xbrli:unit>',
        '<xbrli:context id="FY"><xbrli:entity><xbrli:identifier scheme='
        '"https://www.nseindia.com/NSESymbol">'+symbol+
        '</xbrli:identifier></xbrli:entity><xbrli:period>'
        '<xbrli:startDate>2025-04-01</xbrli:startDate>'
        '<xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period>'
        '<xbrli:scenario><xbrldi:explicitMember '
        'dimension="rep:NatureOfReportAxis">rep:ConsolidatedMember'
        '</xbrldi:explicitMember></xbrli:scenario></xbrli:context>',
        '<xbrli:context id="AY"><xbrli:entity><xbrli:identifier scheme='
        '"https://www.nseindia.com/NSESymbol">'+symbol+
        '</xbrli:identifier></xbrli:entity><xbrli:period>'
        '<xbrli:instant>2026-03-31</xbrli:instant></xbrli:period>'
        '<xbrli:scenario><xbrldi:explicitMember '
        'dimension="rep:NatureOfReportAxis">rep:ConsolidatedMember'
        '</xbrldi:explicitMember></xbrli:scenario></xbrli:context>',
        '<NatureOfReportStandaloneConsolidated>'+basis+
        '</NatureOfReportStandaloneConsolidated>',
        '<Symbol>'+symbol+'</Symbol>',
        f'<RevenueFromOperations contextRef="FY" unitRef="INR">'
        f'{revenue}</RevenueFromOperations>',
        f'<ProfitLossForPeriod contextRef="FY" unitRef="INR">'
        f'{profit}</ProfitLossForPeriod>',
        '<Assets contextRef="AY" unitRef="INR">2000000000</Assets>',
        '<NetCashFlowsFromUsedInOperatingActivities contextRef="FY" '
        f'unitRef="INR">{cash}</NetCashFlowsFromUsedInOperatingActivities>',
    ]
    if duplicate:
        nodes.append(
            '<RevenueFromOperations contextRef="FY" unitRef="INR">'
            '900000000</RevenueFromOperations>'
        )
    nodes.append('</root>')
    return ''.join(nodes).encode()


class FakeResponse:
    def __init__(self,data,code=200):
        self.status_code=code
        self.data=data
    def __enter__(self):
        return self
    def __exit__(self,*_):
        return False
    def iter_content(self,chunk_size=65536):
        for start in range(0,len(self.data),49):
            yield self.data[start:start+49]


class FakeSession:
    def __init__(self,data,code=200):
        self.calls=[]
        self.body=data
        self.code=code
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        return FakeResponse(self.body,self.code)
    def close(self):
        pass


class RawNSEAuditTests(unittest.TestCase):
    def test_four_metrics_exact_period_inr_verified_source_aliases(self):
        result=inspect_xml(
            valid_xml(),symbol=SYMBOL,fiscal_year="2025-26",
            official_values={
                "revenue":100,
                "net_profit":25,
                "total_assets":200,
                "operating_cash_flow":20,
            }
        )
        self.assertTrue(result["xml_global_consolidated_basis_present"])
        self.assertTrue(result["xml_exact_company_symbol_present"])
        for metric in ("revenue","net_profit","total_assets",
                       "operating_cash_flow"):
            info=result["metric_source_inspection"][metric]
            self.assertEqual(
                info["verdict"],"RAW_XBRL_FACT_AGREES_WITH_ARCHIVED_NSE_VALUE"
            )
            self.assertEqual(info["candidate_inr_exact_period_count"],1)
            self.assertTrue(info["raw_nse_value_found_among_candidates"])
            self.assertFalse(info["automatic_numeric_correction"])

    def test_detects_conflicting_same_year_revenue_facts(self):
        result=inspect_xml(
            valid_xml(duplicate=True),symbol=SYMBOL,
            fiscal_year="2025-26",official_values={"revenue":100}
        )
        info=result["metric_source_inspection"]["revenue"]
        self.assertEqual(info["verdict"],
                         "MULTIPLE_DISTINCT_INR_FACTS_REQUIRE_TAG_REVIEW")
        self.assertEqual(info["candidate_distinct_crore_values"],[90.,100.])

    def test_missing_inr_unit_makes_numerical_match_unverifiable(self):
        info=inspect_xml(
            valid_xml(currency="iso4217:USD"),
            symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100}
        )["metric_source_inspection"]["revenue"]
        self.assertEqual(info["verdict"],"NO_EXACT_CONTEXT_INR_FACT_FOR_METRIC")
        self.assertEqual(info["wrong_unit_rejected"],1)
        self.assertFalse(info["raw_nse_value_found_among_candidates"])

    def test_wrong_period_and_global_standalone_rejected(self):
        result=inspect_xml(
            valid_xml(basis="Standalone"),symbol=SYMBOL,
            fiscal_year="2025-26",official_values={"revenue":100}
        )
        self.assertEqual(
            result["metric_source_inspection"]["revenue"]["verdict"],
            "DOCUMENT_REPORTING_BASIS_OR_SYMBOL_NOT_PROVEN"
        )
        # FY2024-25 requires exact Apr 2024-Mar 2025, not the FY2025-26
        # document's annual duration.
        missing=inspect_xml(
            valid_xml(),symbol=SYMBOL,fiscal_year="2024-25",
            official_values={"revenue":100}
        )
        self.assertEqual(
            missing["metric_source_inspection"]["revenue"]["verdict"],
            "NO_EXACT_CONTEXT_INR_FACT_FOR_METRIC"
        )

    def test_wrong_symbol_and_billion_limited_document_fail_closed(self):
        info=inspect_xml(
            valid_xml(symbol="SOMEBODYELSE"),
            symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
        )
        self.assertFalse(info["xml_exact_company_symbol_present"])
        self.assertEqual(
            info["metric_source_inspection"]["revenue"]["verdict"],
            "DOCUMENT_REPORTING_BASIS_OR_SYMBOL_NOT_PROVEN"
        )
        with self.assertRaisesRegex(DrilldownError,"XML_SIZE_OR_TYPE"):
            inspect_xml(b"x"*5_000_001,symbol=SYMBOL,
                        fiscal_year="2025-26",official_values={"revenue":100})

    def test_doctype_or_entity_injection_rejected(self):
        evil=b'<!DOCTYPE root [<!ENTITY exploit SYSTEM "file:///etc/passwd">]><root/>'
        with self.assertRaisesRegex(DrilldownError,"ENTITY_DECLARATION"):
            inspect_xml(
                evil,symbol=SYMBOL,fiscal_year="2025-26",
                official_values={"revenue":100}
            )

    def test_download_must_be_official_xbrl_source_without_token(self):
        sess=FakeSession(valid_xml())
        data,issue=_download(sess,BASE)
        self.assertIsNone(issue)
        self.assertEqual(data,valid_xml())
        url,kwargs=sess.calls[0]
        self.assertEqual(url,BASE)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(kwargs["stream"])
        self.assertNotIn("Authorization",kwargs["headers"])
        with self.assertRaisesRegex(DrilldownError,"NOT_ORIGINAL_NSE"):
            _download(sess,"https://fake.nseindia.com/equity.xml")
        self.assertEqual(len(sess.calls),1)

    def test_one_blocking_http_403_stops_further_nse_requests(self):
        with tempfile.TemporaryDirectory() as td:
            # This test uses a pure synthetic triager/annual-source loader.
            root=Path(td)
            report_folder=_stored_paths(root)[0]/"reconciliation"
            report_folder.mkdir(parents=True)
            with (report_folder/"source_discrepancy_forensics.csv").open(
                "w",newline=""
            ) as f:
                w=csv.DictWriter(f,fieldnames=[
                    "symbol","financial_year","nse_filing_url",
                    "metric","verified_nse_crore",
                ])
                w.writeheader()
                w.writerows([{
                    "symbol":SYMBOL,"financial_year":"2025-26",
                    "nse_filing_url":BASE,"metric":"revenue",
                    "verified_nse_crore":100
                }])
            def triager(_root):
                return {
                    "scope":"UPSTOX_NSE_DIFFERENCE_FORENSIC_TRIAGE_ONLY_V1",
                    "upstox_companies_checked":1,"metric_discrepancies":1
                }
            def history(_root):
                return {(SYMBOL,"2025-26"):original_row("2025-26",url=BASE)},{}
            # official source test row defaults Revenue_Operations_Cr =100
            sess=FakeSession(b"",code=403)
            result=drilldown(
                root,session=sess,pause=lambda _:None,
                triager=triager,history_loader=history,
            )
            self.assertEqual(result["original_nse_filing_urls_attempted"],1)
            self.assertTrue(result["nse_download_blocked"])
            self.assertEqual(len(sess.calls),1)
            self.assertEqual(
                result["sample_file_reviews"][0]["fetch_state"],
                "NSE_XBRL_HTTP_403"
            )
            self.assertFalse(result["original_nse_values_modified"])
            self.assertEqual(result["live_orders"],"DISABLED")

    def test_one_successful_original_nse_xml_source_verdict(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            folder=_stored_paths(root)[0]/"reconciliation"
            folder.mkdir(parents=True)
            with (folder/"source_discrepancy_forensics.csv").open(
                "w",newline=""
            ) as f:
                writer=csv.DictWriter(f,fieldnames=[
                    "symbol","financial_year","nse_filing_url",
                    "metric","verified_nse_crore",
                ])
                writer.writeheader()
                writer.writerow({
                    "symbol":SYMBOL,"financial_year":"2025-26",
                    "nse_filing_url":BASE,"metric":"revenue",
                    "verified_nse_crore":100
                })
            def triager(_root):
                return {
                    "scope":"UPSTOX_NSE_DIFFERENCE_FORENSIC_TRIAGE_ONLY_V1",
                    "upstox_companies_checked":1,"metric_discrepancies":1
                }
            def history(_root):
                return {(SYMBOL,"2025-26"):original_row("2025-26",url=BASE)},{}
            session=FakeSession(valid_xml())
            result=drilldown(
                root,max_files=1,session=session,pause=lambda _:None,
                triager=triager,history_loader=history,
            )
            self.assertEqual(result["metric_drilldown_verdict_counts"],{
                "RAW_XBRL_FACT_AGREES_WITH_ARCHIVED_NSE_VALUE":1
            })
            self.assertEqual(result["original_nse_filing_urls_attempted"],1)
            self.assertFalse(result["nse_download_blocked"])
            path=folder/"nse_xbrl_drilldown.json"
            self.assertTrue(path.exists())
            self.assertEqual(json.loads(path.read_text())["live_orders"],
                             "DISABLED")

    def test_max_four_original_urls_hard_cap(self):
        with tempfile.TemporaryDirectory() as td:
            for limit in (0,5,100,False,True,-1):
                with self.assertRaises(ValueError):
                    drilldown(td,max_files=limit)
            self.assertFalse(
                list(Path(td).rglob("nse_xbrl_drilldown.json"))
            )


if __name__=="__main__":
    unittest.main()
