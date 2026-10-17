"""Offline regressions for source-attested official NSE OLD annual XBRL discovery."""
import csv
import requests
from datetime import datetime,timezone,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nse_legacy_annual_official_auto_index import (
    API,LEDGER,LEDGER_FIELDS,STATUS,run,source_metadata,LegacyIndexUnavailable,
    official_link,query,DIAGNOSTIC_VERSION,
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
        (self.root/LEDGER).parent.mkdir(parents=True,exist_ok=True)
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
        self.assertEqual(opts["params"],{
            "index":"equities","period":"Annual","symbol":SYMBOL,
            "from_date":"01-04-2024","to_date":"30-09-2024"})
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

    def test_2022_23_target_uses_only_2023_broadcast_window(self):
        # A source-verified PDF from 2022-23 authorizes a single query
        # restricted to that FY's normal first filing season.
        self.source.stop()
        src=patch("nse_legacy_annual_official_auto_index.checked_sources",
                  return_value=[({
                      "Symbol":SYMBOL,"Financial_Year":"2022-23",
                      "Official_Report_URL":
                      "https://nsearchives.nseindia.com/annual_reports/"
                      "AR_20000_20MICRONS_2022_2023_20062023104857.pdf",
                      "PDF_SHA256":"b"*64,
                  },{"ISIN":ISIN},self.root/"cached-fy22-23.pdf")])
        src.start()
        self.addCleanup(src.stop)
        session=FakeSession([annual_row(year="2022-23")])
        result=run(self.root,max_symbols=1,now=NOW,
                   session=session,pause=lambda _:None)
        self.assertEqual(result["api_calls_made_this_run"],1)
        self.assertEqual(session.calls[0][1]["params"],{
            "index":"equities","period":"Annual","symbol":SYMBOL,
            "from_date":"01-04-2023","to_date":"30-09-2023"})
        self.assertEqual(result["last_target_fiscal_year"],"2022-23")
        self.assertEqual(result["last_broadcast_window_start"],"2023-04-01")
        self.assertEqual(result["last_broadcast_window_end"],"2023-09-30")
        self.assertEqual(len(self.rows("2022-23")),1)
        self.protected()

    def test_real_read_timeout_remains_blocked_with_20hr_cooldown(self):
        class ReadTimeoutSession:
            calls=0
            def get(self,*args,**kwargs):
                self.calls+=1
                raise requests.exceptions.ReadTimeout("SECRET_UNSAFE")
        first_session=ReadTimeoutSession()
        first=run(self.root,max_symbols=1,now=NOW,
                  session=first_session,pause=lambda _:None)
        self.assertEqual(first["errors"][0]["reason"],
                         "OFFICIAL_NSE_OLD_RESULTS_READ_TIMEOUT")
        self.assertEqual(first_session.calls,1)
        self.assertEqual(first["new_official_old_annual_xbrl_links_indexed"],0)
        self.assertEqual(first["last_target_fiscal_year"],"2023-24")
        second_session=ReadTimeoutSession()
        second=run(self.root,max_symbols=1,now=NOW+timedelta(minutes=12),
                   session=second_session,pause=lambda _:None)
        self.assertEqual(second_session.calls,0)
        self.assertEqual(second["state"],
                         "OFFICIAL_NSE_OLD_ANNUAL_INDEX_COOLDOWN_NO_REQUEST")
        self.assertEqual(second["symbols_deferred_by_cooldown"],1)
        self.assertEqual(second["next_eligible_at_utc"],
                         (NOW+timedelta(hours=20)).isoformat())
        self.assertEqual(second["symbols_remaining_after_this_run"],1)
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

    def test_opaque_old_failure_gets_only_one_delayed_diagnostic_reprobe(self):
        # Local user saw ambiguous NETWORK_OR_JSON_ERROR. A legacy ledger
        # can have exactly one more request under the new diagnostic code,
        # never an immediate rapid-fire attempt or unblock of HTTP 403.
        previous={
            "Symbol":SYMBOL,"Checked_At_UTC":(
                NOW-timedelta(minutes=12)).isoformat(),
            "State":"INDEX_DISCOVERY_BLOCKED","Rows_Indexed":0,
            "Reason":"OFFICIAL_NSE_OLD_RESULTS_NETWORK_OR_JSON_ERROR",
        }
        with (self.root/LEDGER).open("w",newline="") as f:
            writer=csv.DictWriter(f,fieldnames=list(previous))
            writer.writeheader()
            writer.writerow(previous)
        session=FakeSession([annual_row()])
        summary=run(self.root,max_symbols=1,now=NOW,session=session,pause=lambda _:None)
        self.assertEqual(summary["api_calls_made_this_run"],1)
        self.assertEqual(summary["new_official_old_annual_xbrl_links_indexed"],1)
        self.assertEqual(len(session.calls),1)
        repeat_session=FakeSession([])
        repeat=run(self.root,max_symbols=1,now=NOW+timedelta(minutes=2),
                   session=repeat_session,pause=lambda _:None)
        self.assertEqual(repeat["api_calls_made_this_run"],0)
        self.assertEqual(len(repeat_session.calls),0)
        with (self.root/LEDGER).open(newline="") as f:
            item=next(csv.DictReader(f))
        self.assertEqual(item["Diagnostic_Version"],DIAGNOSTIC_VERSION)
        self.protected()

    def test_user_20microns_generic_error_reports_cooldown_not_updated(self):
        # Real user first checked 2026-10-17T10:34:09Z and re-ran at
        # 10:39:34Z. No new XBRL link can be claimed after zero requests.
        earlier=datetime.fromisoformat("2026-10-17T10:34:09+00:00")
        check=datetime.fromisoformat("2026-10-17T10:39:34+00:00")
        with (self.root/LEDGER).open("w",newline="") as f:
            writer=csv.DictWriter(f,fieldnames=[
                "Symbol","Checked_At_UTC","State","Rows_Indexed","Reason"])
            writer.writeheader()
            writer.writerow({
                "Symbol":SYMBOL,"Checked_At_UTC":earlier.isoformat(),
                "State":"INDEX_DISCOVERY_BLOCKED","Rows_Indexed":0,
                "Reason":"OFFICIAL_NSE_OLD_RESULTS_NETWORK_OR_JSON_ERROR",
            })
        session=FakeSession([annual_row()])
        result=run(self.root,max_symbols=1,now=check,
                   session=session,pause=lambda _:None)
        self.assertEqual(result["state"],
                         "OFFICIAL_NSE_OLD_ANNUAL_INDEX_COOLDOWN_NO_REQUEST")
        self.assertEqual(result["api_calls_made_this_run"],0)
        self.assertEqual(result["symbols_requested_this_run"],0)
        self.assertEqual(result["symbols_fetched_this_run"],0)
        self.assertEqual(result["symbols_deferred_by_cooldown"],1)
        self.assertEqual(result["symbols_remaining_after_this_run"],1)
        self.assertEqual(result["next_eligible_at_utc"],
                         "2026-10-17T10:44:09+00:00")
        self.assertEqual(result["new_official_old_annual_xbrl_links_indexed"],0)
        self.assertEqual(session.calls,[])
        self.protected()

    def test_versioned_error_uses_full_cooldown_no_second_probing(self):
        earlier=NOW-timedelta(minutes=11)
        with (self.root/LEDGER).open("w",newline="") as f:
            fields=[
                "Symbol","Checked_At_UTC","State","Rows_Indexed",
                "Reason","Diagnostic_Version"]
            writer=csv.DictWriter(f,fieldnames=fields)
            writer.writeheader()
            writer.writerow({
                "Symbol":SYMBOL,"Checked_At_UTC":earlier.isoformat(),
                "State":"INDEX_DISCOVERY_BLOCKED","Rows_Indexed":0,
                "Reason":"OFFICIAL_NSE_OLD_RESULTS_INVALID_JSON_200_RESPONSE",
                "Diagnostic_Version":DIAGNOSTIC_VERSION,
            })
        session=FakeSession([annual_row()])
        result=run(self.root,max_symbols=1,now=NOW,
                   session=session,pause=lambda _:None)
        self.assertEqual(result["symbols_deferred_by_cooldown"],1)
        self.assertEqual(result["next_eligible_at_utc"],
                         (earlier+timedelta(hours=20)).isoformat())
        self.assertEqual(result["state"],
                         "OFFICIAL_NSE_OLD_ANNUAL_INDEX_COOLDOWN_NO_REQUEST")
        self.assertEqual(session.calls,[])
        self.protected()

    def test_previous_error_not_reprobed_too_soon_or_when_403(self):
        for age,reason in [
            (timedelta(minutes=2),"OFFICIAL_NSE_OLD_RESULTS_NETWORK_OR_JSON_ERROR"),
            (timedelta(hours=3),"OFFICIAL_NSE_OLD_RESULTS_ACCESS_BLOCKED_403"),
        ]:
            with self.subTest(age=age,reason=reason):
                with (self.root/LEDGER).open("w",newline="") as f:
                    writer=csv.DictWriter(f,fieldnames=[
                        "Symbol","Checked_At_UTC","State","Rows_Indexed","Reason"])
                    writer.writeheader()
                    writer.writerow({
                        "Symbol":SYMBOL,
                        "Checked_At_UTC":(NOW-age).isoformat(),
                        "State":"INDEX_DISCOVERY_BLOCKED",
                        "Rows_Indexed":0,
                        "Reason":reason,
                    })
                session=FakeSession([annual_row()])
                result=run(self.root,max_symbols=1,now=NOW,session=session,pause=lambda _:None)
                self.assertEqual(result["api_calls_made_this_run"],0)
                self.assertEqual(session.calls,[])
        self.protected()

    def test_200_html_not_json_and_bad_json_are_separate_safe_errors(self):
        class BadResponse:
            status_code=200
            headers={"Content-Type":"text/html; charset=utf-8"}
            content=b"<html>EXPOSED_TOKEN_SHOULD_NOT_BE_REPORTED</html>"
            def json(self):
                raise ValueError("HTML response contains EXPOSED_TOKEN_SHOULD_NOT_BE_REPORTED")
        class BadJSON:
            status_code=200
            headers={"Content-Type":"application/json"}
            content=b"not actually json SECRET_SHOULD_NOT_LEAK"
            def json(self):
                raise ValueError("SECRET_SHOULD_NOT_LEAK")
        class TestSession:
            def __init__(self,response):
                self.response=response
                self.calls=0
            def get(self,*args,**kwargs):
                self.calls+=1
                return self.response
        for response,reason in [
            (BadResponse(),"OFFICIAL_NSE_OLD_RESULTS_HTML_INSTEAD_OF_JSON"),
            (BadJSON(),"OFFICIAL_NSE_OLD_RESULTS_INVALID_JSON_200_RESPONSE"),
        ]:
            with self.subTest(reason=reason):
                session=TestSession(response)
                with self.assertRaises(LegacyIndexUnavailable) as context:
                    query(session,SYMBOL)
                self.assertEqual(str(context.exception),reason)
                self.assertNotIn("SECRET",str(context.exception))
                self.assertNotIn("EXPOSED_TOKEN",str(context.exception))
                self.assertEqual(session.calls,1)

    def test_network_timeout_tls_and_connection_separately_classified(self):
        class ThrowSession:
            def __init__(self,exc):
                self.exc=exc
                self.calls=0
            def get(self,*args,**kwargs):
                self.calls+=1
                raise self.exc
        for exc,reason in [
            (requests.exceptions.ConnectTimeout("leaky-host"),"CONNECT_TIMEOUT"),
            (requests.exceptions.ReadTimeout("leaky-host"),"READ_TIMEOUT"),
            (requests.exceptions.SSLError("secret-ip"),"TLS_ERROR"),
            (requests.exceptions.ConnectionError("host secret"),"CONNECTION_ERROR"),
        ]:
            with self.subTest(reason=reason):
                session=ThrowSession(exc)
                with self.assertRaisesRegex(LegacyIndexUnavailable,reason) as context:
                    query(session,SYMBOL)
                self.assertNotIn("secret",str(context.exception).lower())
                self.assertNotIn("leaky",str(context.exception).lower())
                self.assertEqual(session.calls,1)

    def test_bounds_fail_closed(self):
        with self.assertRaisesRegex(ValueError,"MAX_SYMBOLS"):
            run(self.root,max_symbols=4,now=NOW,session=FakeSession([]))
        self.protected()


if __name__=="__main__":
    unittest.main()
