"""Offline preflight regressions for original annual NSE XML index candidates."""
import csv
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nse_legacy_xbrl_preflight import snapshot,OUTPUT,PreflightBlocked
from nse_mainboard_discovery import CATALOG
from nse_legacy_xbrl_verify import LEDGER
from nse_legacy_annual_evidence_queue import STATUS as QUEUE_STATUS

NOW=datetime(2026,10,17,12,tzinfo=timezone.utc)
URL_20_24="https://nsearchives.nseindia.com/corporate/xbrl/20MICRONS_20062024.xml"
URL_20_23="https://nsearchives.nseindia.com/corporate/xbrl/20MICRONS_20062023.xml"
URL_SECOND="https://nsearchives.nseindia.com/corporate/xbrl/SECOND_20062024.xml"
URL_THIRD="https://nsearchives.nseindia.com/corporate/xbrl/THIRD_20062024.xml"
SYMS=[
    ("20MICRONS","INE144J01027"),
    ("SECOND","INE111A01027"),
    ("THIRD","INE222A01027"),
]
INDEX_FIELDS=[
    "Symbol","Period Ended","Audited / Unaudited",
    "Consolidated / Non-Consolidated","Period","XBRL URL",
    "Broadcast Date",
]
LEDGER_FIELDS=[
    "Symbol","Financial_Year","Source_URL","Index_SHA256",
    "Source_SHA256","Checked_At_UTC","Parser_Version",
    "Status","Reason",
]


def write_rows(path,fields,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as file:
        writer=csv.DictWriter(file,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def old_row(sym,year,url,publication):
    return {
        "Symbol":sym,"Period Ended":
            "2024-03-31" if year=="2023-24" else "2023-03-31",
        "Audited / Unaudited":"Audited",
        "Consolidated / Non-Consolidated":"Consolidated",
        "Period":"Annual",
        "XBRL URL":url,"Broadcast Date":publication,
    }


class OldNSEAnnualXMLPreflightTests(unittest.TestCase):
    def setUp(self):
        t=TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        catalog=[
            {"Symbol":sym,"ISIN":isin,"Company":sym+" Limited",
             "Series":"EQ","Baseline_Tracked":"true"}
            for sym,isin in SYMS
        ]
        write_rows(self.root/CATALOG,list(catalog[0]),catalog)
        for name in ("FY2425.csv","NSE_FY2025-26_Consolidated_Fundamentals.csv"):
            write_rows(self.root/name,["Symbol","Company"],[
                {"Symbol":sym,"Company":sym+" Limited"} for sym,_ in SYMS
            ])
        self.verified=patch(
            "nse_legacy_xbrl_preflight.verified_discovered_symbols",
            return_value={sym for sym,_ in SYMS})
        self.verified.start()
        self.addCleanup(self.verified.stop)
        self.fy24=[
            old_row("20MICRONS","2023-24",URL_20_24,"20-Jun-2024"),
            old_row("SECOND","2023-24",URL_SECOND,""),
            old_row("THIRD","2023-24",URL_THIRD,"20-Jun-2024"),
        ]
        self.fy23=[
            old_row("20MICRONS","2022-23",URL_20_23,"20-Jun-2023"),
        ]
        self.save_indexes()
        self.saved_attempt={
            "Symbol":"20MICRONS","Financial_Year":"2022-23",
            "Source_URL":URL_20_23,
            "Checked_At_UTC":(NOW-timedelta(minutes=30)).isoformat(),
            "Status":"ACCESS_BLOCKED","Reason":"SOURCE_HTTP_403",
            "Parser_Version":"source-verifier-v1",
        }
        write_rows(self.root/LEDGER,LEDGER_FIELDS,[self.saved_attempt])
        (self.root/"annual_historical_results.csv").write_text(
            "NEVER_PROMOTE_XBRL_HISTORY_FROM_INDEX")
        (self.root/"swing_watchlist.json").write_text("ORDERS_DISABLED")
        (self.root/"final_fundamental_shortlist.csv").write_text("NO_TRADING")

    def save_indexes(self):
        write_rows(self.root/"nse_legacy_financial_results_2022-23.csv",
                   INDEX_FIELDS,self.fy23)
        write_rows(self.root/"nse_legacy_financial_results_2023-24.csv",
                   INDEX_FIELDS,self.fy24)

    def rows(self):
        with (self.root/OUTPUT).open(newline="",encoding="utf-8") as file:
            return {(r["Symbol"],r["Financial_Year"]):r for r in csv.DictReader(file)}

    def protected(self):
        self.assertEqual((self.root/"annual_historical_results.csv").read_text(),
                         "NEVER_PROMOTE_XBRL_HISTORY_FROM_INDEX")
        self.assertEqual((self.root/"swing_watchlist.json").read_text(),
                         "ORDERS_DISABLED")
        self.assertEqual((self.root/"final_fundamental_shortlist.csv").read_text(),
                         "NO_TRADING")

    def test_annual_metadata_q4_unverified_and_prior_403_cooldown(self):
        result=snapshot(self.root,now=NOW)
        self.assertEqual(result["tracked_baseline_company_years_reviewed"],6)
        self.assertEqual(result["new_independently_verified_xml_facts"],0)
        self.assertEqual(result["original_nse_api_calls"],0)
        self.assertEqual(result["strict_fundamental_passes_added"],0)
        self.assertEqual(result["orders"],"DISABLED")
        rows=self.rows()
        self.assertEqual(rows[("20MICRONS","2023-24")]["Source_Review_State"],
                         "READY_FOR_INDEPENDENT_ORIGINAL_XML_CONTEXT_REVIEW")
        self.assertEqual(rows[("20MICRONS","2023-24")]["Metadata_Ready_For_XML"],
                         "true")
        self.assertEqual(rows[("20MICRONS","2022-23")]["Source_Review_State"],
                         "PREVIOUS_SOURCE_ATTEMPT_COOLDOWN")
        self.assertEqual(rows[("20MICRONS","2022-23")]["Source_Retry_Not_Before_UTC"],
                         (NOW+timedelta(hours=19,minutes=30)).isoformat())
        self.assertEqual(rows[("SECOND","2023-24")]["Source_Review_State"],
                         "SOURCE_INDEX_PUBLICATION_DATE_MISSING_OR_AMBIGUOUS")
        self.assertEqual(rows[("SECOND","2022-23")]["Source_Review_State"],
                         "NO_USABLE_OLD_ANNUAL_XBRL_INDEX_IN_TRACKED_BASELINE")
        self.assertEqual(rows[("THIRD","2023-24")]["Metadata_Ready_For_XML"],
                         "true")
        self.assertTrue(all(r["Independent_Audited_Consolidated_Annual_XML_Checked_This_Run"]=="false"
                            for r in rows.values()))
        self.protected()

    def test_same_url_for_two_issuers_blocks_both(self):
        self.fy24[2]["XBRL URL"]=URL_20_24
        self.save_indexes()
        snapshot(self.root,now=NOW)
        rows=self.rows()
        self.assertEqual(rows[("20MICRONS","2023-24")]["Source_Review_State"],
                         "SHARED_OR_AMBIGUOUS_XML_SOURCE_URL")
        self.assertEqual(rows[("THIRD","2023-24")]["Source_Review_State"],
                         "SHARED_OR_AMBIGUOUS_XML_SOURCE_URL")
        self.assertEqual(rows[("20MICRONS","2023-24")]["Metadata_Ready_For_XML"],
                         "false")
        self.protected()

    def test_duplicate_exact_annual_metadata_rows_with_same_date_block(self):
        # Two different or duplicate index entries cannot be accepted
        # merely because their publication dates are equal.
        self.fy24.append(dict(self.fy24[0]))
        self.save_indexes()
        result=snapshot(self.root,now=NOW)
        row=self.rows()[("20MICRONS","2023-24")]
        self.assertEqual(row["Source_Index_Rows_Matched"],"2")
        self.assertEqual(row["Source_Review_State"],
                         "SOURCE_INDEX_ROW_OR_PUBLICATION_AMBIGUOUS")
        self.assertEqual(row["Metadata_Ready_For_XML"],"false")
        self.assertEqual(result["new_independently_verified_xml_facts"],0)
        self.protected()

    def test_noise_from_other_companies_cannot_match_wrong_symbol(self):
        # Source files contain thousands of issuers: the source index
        # must still match BOTH the exact NSE symbol and original URL.
        for index in range(150):
            self.fy24.append(old_row(
                f"NOISE{index}","2023-24",
                f"https://nsearchives.nseindia.com/corporate/xbrl/NOISE{index}.xml",
                "20-Jun-2024"))
        self.save_indexes()
        snapshot(self.root,now=NOW)
        row=self.rows()[("20MICRONS","2023-24")]
        self.assertEqual(row["Source_Index_Rows_Matched"],"1")
        self.assertEqual(row["Metadata_Ready_For_XML"],"true")
        self.protected()

    def test_label_annual_with_wrong_fiscal_end_not_indexed(self):
        self.fy24[0]["Period Ended"]="2024-06-30"
        self.save_indexes()
        snapshot(self.root,now=NOW)
        self.assertEqual(self.rows()[("20MICRONS","2023-24")]["Source_Review_State"],
                         "NO_USABLE_OLD_ANNUAL_XBRL_INDEX_IN_TRACKED_BASELINE")
        self.protected()

    def test_earlier_xml_context_rejection_not_automatically_retried(self):
        self.saved_attempt["Source_URL"]=URL_20_24
        self.saved_attempt["Financial_Year"]="2023-24"
        self.saved_attempt["Checked_At_UTC"]=(NOW-timedelta(days=3)).isoformat()
        self.saved_attempt["Status"]="UNVERIFIED"
        self.saved_attempt["Reason"]="FULL_ANNUAL_CONTEXT_NOT_PRESENT_Q4_ONLY"
        write_rows(self.root/LEDGER,LEDGER_FIELDS,[self.saved_attempt])
        snapshot(self.root,now=NOW)
        self.assertEqual(self.rows()[("20MICRONS","2023-24")]["Source_Review_State"],
                         "EARLIER_XML_CONTEXT_OR_SCHEMA_REJECTION")
        self.protected()

    def test_conflicting_revisions_quarantined(self):
        self.fy24.append(old_row(
            "20MICRONS","2023-24",
            "https://nsearchives.nseindia.com/corporate/xbrl/REVISED.xml",
            "21-Jun-2024"))
        self.save_indexes()
        snapshot(self.root,now=NOW)
        self.assertEqual(self.rows()[("20MICRONS","2023-24")]["Source_Review_State"],
                         "MULTIPLE_OFFICIAL_XBRL_REVISIONS_NEED_REVIEW")
        self.protected()

    def test_malformed_catalog_leaves_old_output_unchanged(self):
        snapshot(self.root,now=NOW)
        old=(self.root/OUTPUT).read_bytes()
        bad=[
            {"Symbol":sym,"ISIN":isin,"Company":sym+" Limited",
             "Series":"EQ","Baseline_Tracked":"true"} for sym,isin in SYMS[:2]
        ]
        write_rows(self.root/CATALOG,list(bad[0]),bad)
        with self.assertRaisesRegex(PreflightBlocked,"EQ_CURRENT_OFFICIAL"):
            snapshot(self.root,now=NOW)
        self.assertEqual((self.root/OUTPUT).read_bytes(),old)
        self.protected()


if __name__=="__main__":
    unittest.main()
