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
        'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
        'xmlns:iso4217="http://www.xbrl.org/2003/iso4217">',
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

    def test_forged_currency_prefix_is_not_accepted_as_inr(self):
        forged=valid_xml(currency="evil:INR").replace(
            b'<root ',b'<root xmlns:evil="https://untrusted.example.com/currency" ',1
        )
        info=inspect_xml(
            forged,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100}
        )["metric_source_inspection"]["revenue"]
        self.assertEqual(info["wrong_unit_rejected"],1)
        self.assertFalse(info["raw_nse_value_found_among_candidates"])

    def test_source_archived_value_in_other_dimension_is_diagnostic_not_verified(self):
        sample=valid_xml().replace(
            b'</xbrldi:explicitMember></xbrli:scenario>',
            b'</xbrldi:explicitMember><xbrldi:explicitMember '
            b'dimension="rep:OperatingSegmentsAxis">rep:SteelSegmentMember'
            b'</xbrldi:explicitMember></xbrli:scenario>',1
        )
        result=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
            archived_context_ids={"FY"},
        )
        report=result["metric_source_inspection"]["revenue"]
        self.assertEqual(
            report["verdict"],"ARCHIVED_NSE_VALUE_IN_EXCLUDED_CONTEXT_REVIEW"
        )
        self.assertEqual(report["candidate_inr_exact_period_count"],0)
        self.assertEqual(report["non_basis_dimension_rejected"],1)
        self.assertEqual(report["excluded_matching_facts_in_archived_context_ids"],1)
        self.assertEqual(
            report["excluded_fact_examples_not_verified"][0]["reason"],
            "DIMENSIONED_CONTEXT"
        )
        self.assertFalse(report["automatic_numeric_correction"])

    def test_cin_identifier_cannot_be_mistaken_for_nse_symbol(self):
        sample=valid_xml().replace(
            b'https://www.nseindia.com/NSESymbol',
            b'http://www.mca.gov.in/CIN'
        )
        report=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100}
        )["metric_source_inspection"]["revenue"]
        self.assertEqual(report["candidate_inr_exact_period_count"],0)
        self.assertEqual(report["identifier_scheme_or_symbol_rejected"],1)
        self.assertEqual(
            report["excluded_fact_examples_not_verified"][0]["reason"],
            "CONTEXT_IDENTIFIER_SCHEME_UNVERIFIED"
        )

    def test_mca_cin_self_match_is_not_independent_nse_issuer_verification(self):
        cin="L12345MH2000PLC123456"
        sample=valid_xml().replace(
            b'https://www.nseindia.com/NSESymbol',
            b'https://www.mca.gov.in/CIN'
        ).replace(
            b'>SYNTHETIC</xbrli:identifier>',
            b'>L12345MH2000PLC123456</xbrli:identifier>'
        ).replace(
            b'</root>',
            b'<CorporateIdentityNumber>L12345MH2000PLC123456'
            b'</CorporateIdentityNumber></root>'
        )
        report=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
            archived_context_ids={"revenue":{"FY"}},
        )
        self.assertTrue(report["xml_exact_company_symbol_present"])
        self.assertEqual(report["xml_claimed_cin_count"],1)
        self.assertIn("MCA_CIN_URI",report["context_identifier_scheme_classes"])
        detail=report["metric_source_inspection"]["revenue"]
        self.assertEqual(
            detail["verdict"],"ARCHIVED_NSE_VALUE_IN_EXCLUDED_CONTEXT_REVIEW"
        )
        self.assertEqual(
            detail["excluded_fact_examples_not_verified"][0][
                "document_cin_relation"
            ],"DOCUMENT_CIN_CONTEXT_MATCH_UNVERIFIED_EXTERNAL"
        )
        self.assertEqual(detail["candidate_inr_exact_period_count"],0)
        self.assertTrue(report["legacy_parser_issuer_identity_gap_possible"])
        self.assertFalse(detail["automatic_numeric_correction"])

    def test_conflicting_cin_self_claim_is_review_only(self):
        sample=valid_xml().replace(
            b'https://www.nseindia.com/NSESymbol',
            b'http://www.mca.gov.in/CIN'
        ).replace(
            b'</root>',
            b'<CorporateIdentityNumber>L12345MH2000PLC123456'
            b'</CorporateIdentityNumber></root>'
        )
        report=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
            archived_context_ids={"revenue":{"FY"}},
        )["metric_source_inspection"]["revenue"]
        self.assertEqual(
            report["excluded_fact_examples_not_verified"][0][
                "document_cin_relation"
            ],"DOCUMENT_CIN_CONTEXT_CONFLICTS_WITH_XML_CLAIM"
        )
        self.assertEqual(report["candidate_inr_exact_period_count"],0)

    def test_mca_cin_scheme_alone_does_not_make_verified_fact(self):
        sample=valid_xml().replace(
            b'https://www.nseindia.com/NSESymbol',
            b'https://www.mca.gov.in/CIN'
        )
        report=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
            archived_context_ids={"revenue":{"FY"}},
        )["metric_source_inspection"]["revenue"]
        self.assertEqual(
            report["excluded_fact_examples_not_verified"][0][
                "document_cin_relation"
            ],"DOCUMENT_CIN_FACT_NOT_PRESENT"
        )
        self.assertEqual(report["candidate_inr_exact_period_count"],0)

    def test_per_metric_archive_contexts_do_not_leak_between_cash_and_assets(self):
        sample=valid_xml().replace(
            b'https://www.nseindia.com/NSESymbol',
            b'https://www.mca.gov.in/CIN'
        )
        report=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100,"total_assets":200},
            archived_context_ids={
                "revenue":{"AY"},  # Deliberately wrong archived period
                "total_assets":{"AY"},
            },
        )
        revenue=report["metric_source_inspection"]["revenue"]
        assets=report["metric_source_inspection"]["total_assets"]
        self.assertEqual(
            revenue["verdict"],"RAW_NSE_VALUE_ONLY_IN_EXCLUDED_CONTEXT_REVIEW"
        )
        self.assertEqual(
            assets["verdict"],"ARCHIVED_NSE_VALUE_IN_EXCLUDED_CONTEXT_REVIEW"
        )
        self.assertEqual(revenue["excluded_matching_facts_in_archived_context_ids"],0)
        self.assertEqual(assets["excluded_matching_facts_in_archived_context_ids"],1)
        self.assertEqual(revenue["archived_selected_context_ids_count"],1)
        self.assertEqual(assets["archived_selected_context_ids_count"],1)
        self.assertTrue(report["legacy_parser_issuer_identity_gap_possible"])

    def test_valid_issuer_scheme_is_not_reported_as_legacy_attestation_gap(self):
        report=inspect_xml(
            valid_xml(),symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
            archived_context_ids={"revenue":{"FY"}},
        )
        self.assertFalse(report["legacy_parser_issuer_identity_gap_possible"])
        self.assertEqual(
            report["metric_source_inspection"]["revenue"]["verdict"],
            "RAW_XBRL_FACT_AGREES_WITH_ARCHIVED_NSE_VALUE",
        )

    def test_sebi_scheme_is_not_error_but_value_shape_remains_unverified(self):
        sample=valid_xml().replace(
            b'https://www.nseindia.com/NSESymbol',
            b'https://www.sebi.gov.in'
        )
        outcome=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
            archived_context_ids={"revenue":{"FY"}},
        )
        self.assertEqual(
            outcome["context_identifier_scheme_classes"],
            {"SEBI_SCHEME_URI_NOT_INDEPENDENT_ISSUER_PROOF":2}
        )
        self.assertEqual(
            outcome["context_identifier_value_shape_classes"],
            {"SYMBOL_OR_TEXT_SHAPED_UNVERIFIED":2}
        )
        self.assertTrue(outcome["all_xbrl_contexts_use_same_self_declared_issuer"])
        self.assertEqual(
            outcome["unverified_distinct_context_issuer_identifiers_count"],1
        )
        self.assertEqual(outcome["context_identifiers_equal_filing_symbol_count"],2)
        self.assertTrue(
            outcome["issuer_registration_scheme_is_not_independent_identity_proof"]
        )
        info=outcome["metric_source_inspection"]["revenue"]
        self.assertEqual(
            info["verdict"],"OFFICIAL_NSE_XML_FACT_REPRODUCED_ISSUER_LINK_PENDING"
        )
        self.assertTrue(info["official_xml_value_reproduced_pending_issuer_link"])
        self.assertEqual(info["official_xml_reference_distinct_crore_values"],[100.])
        self.assertFalse(info["issuer_identity_independently_verified"])
        self.assertFalse(info["strict_audited_consolidated_fact_attested_by_this_audit"])
        self.assertTrue(info["excluded_fact_examples_not_verified"][0][
            "issuer_identifier_equals_filing_symbol"
        ])
        self.assertFalse(info["automatic_numeric_correction"])

    def test_bse_scheme_and_cin_shaped_identifier_not_confirmed_as_cin(self):
        cin=b"L12345MH2000PLC123456"
        sample=valid_xml().replace(
            b'https://www.nseindia.com/NSESymbol',
            b'https://www.bseindia.com'
        ).replace(
            b'>SYNTHETIC</xbrli:identifier>',
            b'>'+cin+b'</xbrli:identifier>'
        )
        data=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
            archived_context_ids={"revenue":{"FY"}},
        )
        self.assertEqual(data["context_identifier_scheme_classes"],{
            "BSE_SCHEME_URI_NOT_INDEPENDENT_ISSUER_PROOF":2
        })
        self.assertEqual(data["context_identifier_value_shape_classes"],{
            "CIN_SHAPED_UNVERIFIED":2
        })
        self.assertTrue(data["all_xbrl_contexts_use_same_self_declared_issuer"])
        detail=data["metric_source_inspection"]["revenue"]
        self.assertFalse(detail["excluded_fact_examples_not_verified"][0][
            "issuer_identifier_equals_filing_symbol"
        ])
        self.assertEqual(detail["candidate_inr_exact_period_count"],0)
        self.assertEqual(detail["verdict"],
                         "OFFICIAL_NSE_XML_FACT_REPRODUCED_ISSUER_LINK_PENDING")
        self.assertTrue(detail["official_xml_value_reproduced_pending_issuer_link"])
        self.assertFalse(detail["issuer_identity_independently_verified"])

    def test_sebi_scheme_conflicting_raw_revenue_values_not_marked_reproduced(self):
        sample=valid_xml(duplicate=True).replace(
            b'https://www.nseindia.com/NSESymbol', b'https://www.sebi.gov.in'
        )
        info=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
            archived_context_ids={"revenue":{"FY"}},
        )["metric_source_inspection"]["revenue"]
        self.assertEqual(
            info["verdict"],"OTHER_SCHEME_CONFLICTING_XBRL_FACTS_REQUIRE_REVIEW"
        )
        self.assertEqual(info["official_xml_reference_distinct_crore_values"],
                         [90.,100.])
        self.assertFalse(info["official_xml_value_reproduced_pending_issuer_link"])

    def test_sebi_value_without_matching_archived_context_not_reproduced(self):
        sample=valid_xml().replace(
            b'https://www.nseindia.com/NSESymbol', b'https://www.sebi.gov.in'
        )
        info=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
            archived_context_ids={"revenue":{"AY"}},
        )["metric_source_inspection"]["revenue"]
        self.assertEqual(info["verdict"],
                         "RAW_NSE_VALUE_ONLY_IN_EXCLUDED_CONTEXT_REVIEW")
        self.assertFalse(info["official_xml_value_reproduced_pending_issuer_link"])

    def test_sebi_arbitrary_other_dimension_never_marked_scheme_reproduced(self):
        sample=valid_xml().replace(
            b'https://www.nseindia.com/NSESymbol', b'https://www.sebi.gov.in'
        ).replace(
            b'</xbrldi:explicitMember></xbrli:scenario>',
            b'</xbrldi:explicitMember><xbrldi:explicitMember '
            b'dimension="rep:BusinessSegmentsAxis">rep:SubsidiaryMember'
            b'</xbrldi:explicitMember></xbrli:scenario>',1
        )
        info=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
            archived_context_ids={"revenue":{"FY"}},
        )["metric_source_inspection"]["revenue"]
        self.assertFalse(info["official_xml_value_reproduced_pending_issuer_link"])
        self.assertEqual(info["non_basis_dimension_rejected"],1)
        self.assertEqual(info["verdict"],
                         "ARCHIVED_NSE_VALUE_IN_EXCLUDED_CONTEXT_REVIEW")

    def test_sebi_standalone_global_report_never_reproduced(self):
        sample=valid_xml(basis="Standalone").replace(
            b'https://www.nseindia.com/NSESymbol', b'https://www.sebi.gov.in'
        )
        info=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
            archived_context_ids={"revenue":{"FY"}},
        )["metric_source_inspection"]["revenue"]
        self.assertEqual(info["verdict"],
                         "DOCUMENT_REPORTING_BASIS_OR_SYMBOL_NOT_PROVEN")
        self.assertFalse(info["official_xml_value_reproduced_pending_issuer_link"])

    def test_mixed_context_entities_warn_not_verified_even_on_symbol_match(self):
        sample=valid_xml().replace(
            b'<xbrli:instant>2026-03-31</xbrli:instant>',
            b'<xbrli:instant>2026-03-31</xbrli:instant>',
        ).replace(
            b'>SYNTHETIC</xbrli:identifier>',
            b'>SYNTHETIC</xbrli:identifier>',
            1
        )
        # One context self-identifies a different company; keep the
        # original revenue fact but report context-issuer conflict.
        sample=sample.replace(
            b'>SYNTHETIC</xbrli:identifier>',
            b'>OTHERENTITY</xbrli:identifier>',1
        )
        data=inspect_xml(
            sample,symbol=SYMBOL,fiscal_year="2025-26",
            official_values={"revenue":100},
        )
        self.assertFalse(data["all_xbrl_contexts_use_same_self_declared_issuer"])
        self.assertEqual(
            data["unverified_distinct_context_issuer_identifiers_count"],2
        )
        self.assertTrue(
            data["issuer_registration_scheme_is_not_independent_identity_proof"]
        )
        self.assertEqual(
            data["metric_source_inspection"]["revenue"]["verdict"],
            "DOCUMENT_CONTEXT_ENTITY_IDENTIFIERS_CONFLICT"
        )
        self.assertFalse(
            data["metric_source_inspection"]["revenue"]["automatic_numeric_correction"]
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

    def test_drilldown_end_to_end_counts_sebi_raw_match_without_issuer_attestation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            folder=_stored_paths(root)[0]/"reconciliation"
            folder.mkdir(parents=True)
            with (folder/"source_discrepancy_forensics.csv").open(
                "w",newline=""
            ) as handle:
                writer=csv.DictWriter(handle,fieldnames=[
                    "symbol","financial_year","nse_filing_url",
                    "metric","verified_nse_crore",
                ])
                writer.writeheader()
                writer.writerow({
                    "symbol":SYMBOL,"financial_year":"2025-26",
                    "nse_filing_url":BASE,"metric":"revenue",
                    "verified_nse_crore":100,
                })
            def triager(_root):
                return {
                    "scope":"UPSTOX_NSE_DIFFERENCE_FORENSIC_TRIAGE_ONLY_V1",
                    "upstox_companies_checked":1,"metric_discrepancies":1,
                }
            def history(_root):
                row=original_row("2025-26",url=BASE)
                row["Annual_Context_IDs"]="FY"
                return {(SYMBOL,"2025-26"):row},{}
            session=FakeSession(valid_xml().replace(
                b'https://www.nseindia.com/NSESymbol',
                b'https://www.sebi.gov.in',
            ))
            result=drilldown(
                root,max_files=1,session=session,
                triager=triager,history_loader=history,
                pause=lambda _:None,
            )
            self.assertEqual(result["metric_drilldown_verdict_counts"],{
                "OFFICIAL_NSE_XML_FACT_REPRODUCED_ISSUER_LINK_PENDING":1
            })
            self.assertEqual(
                result["official_nse_xml_numeric_reproductions_pending_issuer"],1
            )
            self.assertEqual(
                result["issuer_identity_attestation_pending_metric_cells"],1
            )
            self.assertFalse(result["issuer_identity_independently_attested"])
            self.assertEqual(result["new_nse_verified_years_added"],0)
            self.assertEqual(result["strict_fundamental_pass_changes"],0)
            self.assertFalse(result["trade_approvals"])
            self.assertEqual(result["live_orders"],"DISABLED")
            saved=json.loads(
                (folder/"nse_xbrl_drilldown.json").read_text()
            )
            self.assertEqual(
                saved["official_nse_xml_numeric_reproductions_pending_issuer"],1
            )

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
