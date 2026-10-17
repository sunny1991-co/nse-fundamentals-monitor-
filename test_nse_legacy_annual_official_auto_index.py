"""Offline regressions for source-attested official NSE OLD annual XBRL discovery."""
import csv
from datetime import datetime,timezone,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nse_legacy_annual_official_auto_index import (
    API,LEDGER,LEDGER_FIELDS,STATUS,run,source_metadata,LegacyIndexUnavailable,
    official_link,
)
import nse_legacy_annual_recovery as legacy

NOW=datetime(2026,10,17,12,tzinfo=timezone.utc)
URL="https://nsearchives.nseindia.com/corporate/xbrl/INDAS_TEST_20062024104857.xml"
SYMBOL="20MICRONS"
ISIN="INE144J01027"


def annual_row(*,year="2023-24",**updates):
    beginning,ending,publication=(
        ("01-Apr-2023","31-Mar-2024","20-Jun-2024 10:48:57")
        if year=="2023-24" else
        ("01-Apr-2022","31-Mar-2023","20-Jun-2023 10:48:57")
    )
    row={
        "symbol":SYMBOL,"isin":ISIN,"period":"Annual",
        "audited":"Audited","consolidated":"Consolidated",
        "fromDate":beginning,"toDate":ending,
        "broadCastDate":publication,
        "financialYear":beginning+" To "+ending,
        "companyName":"20 Microns Limited",
        "xbrl":URL if year=="2023-24" else URL.replace("2024","2023"),
    }
    row.update(updates)
    return row


class FakeResp:
    def __init__(self,rows,status=200):
        self.status_code=status
        self.content=b'{"fake":"bounded"}'
        self.rows=rows

    def json(self):
        return self.rows


class FakeSession:
    def __init__(self,rows,status=200):
        self.rows=rows
        self.status=status
        self.calls=[]
        self.closed=False

    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        return FakeResp(self.rows,self.status)

    def close(self):
        self.closed=True


class AnnualLegacyAutoIndexTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        # The collector never changes source fundamentals, watchlists
        # or brokerage settings.
        (self.root/"annual_historical_results.csv").write_text("DO_NOT_CHANGE")
        (self.root/"final_fundamental_shortlist.csv").write_text("DO_NOT_CHANGE")
        (self.root/"swing_watchlist.json").write_text("DO_NOT_CHANGE")
        with (self.root/"FY2425.csv").open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=["Symbol","Company"])
            w.writeheader()
            w.writerow({"Symbol":SYMBOL,"Company":"20 Microns Limited"})
        self.source=patch(
            "nse_legacy_annual_official_auto_index.checked_sources",
            return_value=[({
                "Symbol":SYMBOL,"Financial_Year":"2023-24",
                "Official_Report_URL":"https://nsearchives.nseindia.com/annual_reports/AR_24150_20MICRONS_2023_2024_20062024104857.pdf",
                "PDF_SHA256":"a"*64,
            },{"ISIN":ISIN},self.root/"cached-original.pdf")])
        self.source.start()
        self.addCleanup(self.source.stop)

    def rows(self,year):
        name=f"nse_legacy_financial_results_{year}.csv"
        with (self.root/name).open(newline="") as f:
            return list(csv.DictReader(f))

    def protected(self):
        self.assertEqual((self.root/"annual_historical_results.csv").read_text(),"DO_NOT_CHANGE")
        self.assertEqual((self.root/"swing_watchlist.json").read_text(),"DO_NOT_CHANGE")
        self.assertEqual((self.root/"final_fundamental_shortlist.csv").read_text(),"DO_NOT_CHANGE")

    def test_2023_24_original_index_is_importable_by_legacy_recovery_only(self):
        session=FakeSession([annual_row()])
        result=run(self.root,max_symbols=1,now=NOW,session=session,pause=lambda _:None)
        self.assertEqual(result["new_official_old_annual_xbrl_links_indexed"],1)
        self.assertEqual(result["recovered_audited_xbrl_facts"],0)
        self.assertFalse(result["access_blocked_without_retry"])
        self.assertEqual(len(session.calls),1)
        url,opts=session.calls[0]
        self.assertEqual(url,API)
        self.assertEqual(opts["params"],
                         {"index":"equities","period":"Annual","symbol":SYMBOL})
        record=self.rows("2023-24")[0]
        self.assertEqual(record["Symbol"],SYMBOL)
        self.assertEqual(record["ISIN"],ISIN)
        self.assertEqual(record["Period"],"Annual")
        self.assertEqual(record["Period Ended"],"2024-03-31")
        self.assertEqual(record["XBRL URL"],URL)
        self.assertTrue(legacy.audited_annual_consolidated(record,"2024-03-31")[0])
        manifest,_=legacy.build(self.root)
        selected=[r for r in manifest if r["Symbol"]==SYMBOL
                  and r["Financial_Year"]=="2023-24"]
        self.assertEqual(selected[0]["Recovery_Status"],
                         "PENDING_INDEPENDENT_XBRL_REVIEW")
        self.assertEqual(selected[0]["Source_URL"],URL)
        self.assertEqual(run(self.root,max_symbols=1,now=NOW,
                             session=FakeSession([]),pause=lambda _:None)[
            "symbols_fetched_this_run"],0)
        self.protected()

    def test_2022_23_and_2023_24_year_indexes_separately_not_quarters(self):
        session=FakeSession([annual_row(),annual_row(year="2022-23"),
                             annual_row(period="Quarterly"),
                             annual_row(audited="Un-Audited")])
        result=run(self.root,max_symbols=1,now=NOW,session=session,pause=lambda _:None)
        self.assertEqual(result["new_official_old_annual_xbrl_links_indexed"],2)
        self.assertEqual(len(self.rows("2022-23")),1)
        self.assertEqual(len(self.rows("2023-24")),1)
        self.protected()

    def test_mixed_isin_and_unaudited_not_accepted(self):
        session=FakeSession([
            annual_row(isin="INE999A01011"),
            annual_row(consolidated="Standalone"),
            annual_row(fromDate="01-Jan-2024",period="Annual"),
            annual_row(xbrl="https://other.example/xbrl/fake.xml"),
            annual_row(broadCastDate="01-Jan-2023"),
        ])
        result=run(self.root,max_symbols=1,now=NOW,session=session,pause=lambda _:None)
        self.assertEqual(result["new_official_old_annual_xbrl_links_indexed"],0)
        self.assertFalse((self.root/"nse_legacy_financial_results_2023-24.csv").exists())
        self.protected()

    def test_http_403_and_429_stop_without_guessing_xbrl_or_retry(self):
        for code in (403,429):
            with self.subTest(code=code):
                session=FakeSession([],status=code)
                result=run(self.root,max_symbols=1,now=NOW,session=session,pause=lambda _:None)
                self.assertTrue(result["access_blocked_without_retry"])
                self.assertEqual(result["new_official_old_annual_xbrl_links_indexed"],0)
                self.assertEqual(len(session.calls),1)
                self.assertFalse((self.root/"nse_legacy_financial_results_2023-24.csv").exists())
                (self.root/LEDGER).unlink()
        self.protected()

    def test_ambiguous_two_xbrl_urls_are_both_indexed_not_verified(self):
        another=URL.replace("TEST","SECOND")
        result=run(self.root,max_symbols=1,now=NOW,
                   session=FakeSession([annual_row(),annual_row(xbrl=another)]),
                   pause=lambda _:None)
        self.assertEqual(result["new_official_old_annual_xbrl_links_indexed"],2)
        manifest,_=legacy.build(self.root)
        record=next(r for r in manifest if r["Symbol"]==SYMBOL
                    and r["Financial_Year"]=="2023-24")
        self.assertEqual(record["Recovery_Status"],"MULTIPLE_SOURCE_REVISIONS_NEED_REVIEW")
        self.protected()

    def test_conflicting_existing_index_receipt_is_not_silently_overwritten(self):
        session=FakeSession([annual_row()])
        run(self.root,max_symbols=1,now=NOW,session=session,pause=lambda _:None)
        p=self.root/"nse_legacy_financial_results_2023-24.csv"
        old=p.read_bytes()
        (self.root/LEDGER).unlink()
        changed=annual_row(broadCastDate="21-Jun-2024 10:48:57")
        result=run(self.root,max_symbols=1,now=NOW+timedelta(hours=24),
                   session=FakeSession([changed]),pause=lambda _:None)
        self.assertEqual(result["state"],"OFFICIAL_NSE_OLD_ANNUAL_INDEX_PARTIAL_OR_BLOCKED")
        self.assertIn("PREEXISTING_INDEX_RECEIPT_CONFLICT",
                      result["errors"][0]["reason"])
        self.assertEqual(p.read_bytes(),old)
        self.protected()

    def test_nonmatching_source_url_and_wrong_financial_year_fails_closed(self):
        self.assertFalse(official_link("http://nsearchives.nseindia.com/corporate/xbrl/a.xml"))
        self.assertFalse(official_link("https://nsearchives.nseindia.com/corporate/xbrl/a.xml?token=x"))
        self.assertFalse(official_link("https://evil.invalid/corporate/xbrl/a.xml"))
        self.assertIsNone(source_metadata(
            annual_row(financialYear="01-Apr-2022 To 31-Mar-2023"),
            SYMBOL,ISIN,now=NOW))
        self.protected()

    def test_bounds_fail_closed(self):
        with self.assertRaisesRegex(ValueError,"MAX_SYMBOLS"):
            run(self.root,max_symbols=4,now=NOW,session=FakeSession([]))
        self.protected()


if __name__=="__main__":
    unittest.main()
