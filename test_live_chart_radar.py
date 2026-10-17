"""Strict offline tests for automatic Upstox V3 chart analysis."""
import csv
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from flask import Flask

from nse_mainboard_discovery import CATALOG
from research_monitor.live_chart_radar import (
    ChartUnavailable,run,normalize_candles,analyze,fetch_1m,STATUS,
)
from research_monitor.live_chart_dashboard import install as install_chart_ui,chart_plot
from project_status import snapshot


NOW=datetime.fromisoformat("2026-10-16T11:02:00+05:30")
TOKEN="synthetic_not_real_upstox_secret"


def synthetic_bars():
    initial=datetime.fromisoformat("2026-10-16T09:15:00+05:30")
    result=[]
    # 106 1m candles; latest starts at 11:00, fully closed by NOW.
    for i in range(106):
        c=100+i*.14
        if i==105:
            c+=3
        result.append([
            (initial+timedelta(minutes=i)).isoformat(),
            c-.13,c+.22,c-.28,c,10000 if i!=105 else 25000,0
        ])
    return result


class MockResponse:
    status_code=200
    def __init__(self,candles,status=200):
        self._candles=candles
        self.status_code=status
        self.content=json.dumps({"status":"success","data":{"candles":candles}}).encode()
    def json(self):
        return {"status":"success","data":{"candles":self._candles}}


class MockSession:
    def __init__(self,candles=None,status=200):
        self.calls=[]
        self.candles=candles if candles is not None else synthetic_bars()
        self.status=status
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        return MockResponse(self.candles,self.status)
    def close(self):
        pass


class ChartResearchTests(unittest.TestCase):
    def setUp(self):
        d=TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root=Path(d.name)
        with (self.root/CATALOG).open("w",newline="") as stream:
            out=csv.DictWriter(stream,fieldnames=["Symbol","ISIN","Series","Company"])
            out.writeheader()
            out.writerow({
                "Symbol":"20MICRONS","ISIN":"INE144J01027","Series":"EQ",
                "Company":"20 Microns Limited",
            })
        self.verification=patch(
            "research_monitor.live_chart_radar.verified_discovered_symbols",
            return_value={"20MICRONS"},
        )
        self.verification.start()
        self.addCleanup(self.verification.stop)
        (self.root/"annual_historical_results.csv").write_text("KEEP_VERIFIED_DATA")
        (self.root/"market_ohlcv.csv").write_text("KEEP_ADJUSTED_DATA")
        (self.root/"swing_watchlist.json").write_text("KEEP_APPROVAL_LIST")

    def assert_no_trading_files_modified(self):
        self.assertEqual((self.root/"annual_historical_results.csv").read_text(),
                         "KEEP_VERIFIED_DATA")
        self.assertEqual((self.root/"market_ohlcv.csv").read_text(),
                         "KEEP_ADJUSTED_DATA")
        self.assertEqual((self.root/"swing_watchlist.json").read_text(),
                         "KEEP_APPROVAL_LIST")

    def test_closed_minute_chart_has_indicators_and_not_a_broker_signal(self):
        s=MockSession()
        with patch.dict(os.environ,{"UPSTOX_ANALYTICS_TOKEN":TOKEN,
                                     "NSE_CHART_SYMBOLS":""}):
            report=run(self.root,now=NOW,open_session=True,session=s,market_open=True)
        self.assertEqual(report["state"],"READ_ONLY_LIVE_CHART_RESEARCH")
        self.assertTrue(report["is_live_minute_close_feed"])
        self.assertEqual(len(report["charts"]),1)
        self.assertEqual(len(s.calls),1)
        self.assertEqual(report["charts"][0]["symbol"],"20MICRONS")
        self.assertIn("rsi14",report["charts"][0])
        self.assertIn("macd_histogram",report["charts"][0])
        self.assertIn("session_vwap",report["charts"][0])
        self.assertIn("previous_20_bar_resistance",report["charts"][0])
        self.assertFalse(report["charts"][0]["trade_eligible"])
        self.assertEqual(report["orders"],"DISABLED")
        self.assertNotIn(TOKEN,(self.root/STATUS).read_text())
        self.assert_no_trading_files_modified()
        status=snapshot(self.root)
        self.assertEqual(status["automatic_live_chart_analyzed_symbols"],1)
        self.assertFalse(status["automatic_live_chart_is_trade_eligible"])

        cached=run(self.root,now=NOW+timedelta(seconds=30),
                   open_session=True,session=s,market_open=True)
        self.assertEqual(len(s.calls),1)
        self.assertEqual(cached["charts_count"],1)
        self.assertFalse(cached["refresh_due"])

    def test_cannot_analyze_insufficient_or_out_of_date_candles(self):
        with self.assertRaisesRegex(ChartUnavailable,"INSUFFICIENT"):
            normalize_candles(synthetic_bars()[:30],"20MICRONS",NOW)
        with self.assertRaisesRegex(ChartUnavailable,"TOO_OLD"):
            normalize_candles(synthetic_bars(),"20MICRONS",
                              NOW+timedelta(minutes=8))
        self.assertEqual(
            normalize_candles(synthetic_bars(),"20MICRONS",NOW)[-1]["c"],
            synthetic_bars()[-1][4]
        )

    def test_current_incomplete_minute_is_discarded(self):
        bars=synthetic_bars()
        bars.append([(NOW-timedelta(seconds=15)).isoformat(),
                     123,124,122,123,10,0])
        clean=normalize_candles(bars,"20MICRONS",NOW)
        self.assertEqual(len(clean),106)

    def test_malformed_prices_or_duplicate_periods_are_blocked(self):
        bad=synthetic_bars()
        bad[-1][2]=bad[-1][4]-1
        with self.assertRaisesRegex(ChartUnavailable,"INVALID_CURRENT"):
            normalize_candles(bad,"20MICRONS",NOW)
        bad=synthetic_bars()
        bad.append(bad[-1][:])
        with self.assertRaisesRegex(ChartUnavailable,"DUPLICATE"):
            normalize_candles(bad,"20MICRONS",NOW)
        bad=synthetic_bars()
        bad[-1][0]="2026-10-15T11:00:00+05:30"
        with self.assertRaisesRegex(ChartUnavailable,"INVALID_CURRENT"):
            normalize_candles(bad,"20MICRONS",NOW)

    def test_403_fails_closed_with_cooldown(self):
        s=MockSession(status=403)
        with patch.dict(os.environ,{"UPSTOX_ANALYTICS_TOKEN":TOKEN,
                                     "NSE_CHART_SYMBOLS":""}):
            r=run(self.root,now=NOW,open_session=True,session=s,market_open=True)
            self.assertTrue(r["source_blocked"])
            self.assertEqual(r["state"],"NO_FRESH_VERIFIED_COMPLETE_INTRADAY_CHARTS")
            self.assertEqual(len(s.calls),1)
            run(self.root,now=NOW+timedelta(seconds=60),open_session=True,
                session=s,market_open=True)
            self.assertEqual(len(s.calls),1)
        self.assert_no_trading_files_modified()

    def test_closed_session_makes_prior_live_candles_stale(self):
        s=MockSession()
        with patch.dict(os.environ,{"UPSTOX_ANALYTICS_TOKEN":TOKEN,
                                     "NSE_CHART_SYMBOLS":""}):
            run(self.root,now=NOW,open_session=True,session=s,market_open=True)
            closed=run(self.root,now=NOW+timedelta(hours=8),
                       open_session=False,session=s)
        self.assertEqual(closed["state"],"EXCHANGE_NOT_LIVE_NO_NEW_CHART_CANDLES")
        self.assertEqual(json.loads((self.root/STATUS).read_text())["charts"],[])
        self.assertEqual(len(s.calls),1)

    def test_mfa_dashboard_denies_unverified_session_and_svg_is_static(self):
        chart=analyze("20MICRONS",normalize_candles(synthetic_bars(),"20MICRONS",NOW))
        bars=chart_plot(chart["chart_last_bars"])
        self.assertEqual(len(bars),50)
        self.assertTrue(all(bar["body_height"]>0 for bar in bars))
        app=Flask(__name__)
        app.secret_key="test-secret"
        app.add_url_rule("/login",endpoint="login",view_func=lambda:"LOGIN")
        class Auth:
            def valid(self,token):
                return token=="TEST_AUTH"
        install_chart_ui(app,Auth(),lambda:self.root)
        client=app.test_client()
        unauth=client.get("/charts")
        self.assertEqual(unauth.status_code,302)
        self.assertEqual(unauth.location,"/login")
        with client.session_transaction() as session:
            session["auth"]="TEST_AUTH"
        ready=client.get("/charts")
        self.assertEqual(ready.status_code,200)
        self.assertIn(b"Orders: DISABLED",ready.data)
        self.assertIn(b"No fresh, verified",ready.data)


if __name__=='__main__':
    unittest.main()
