"""Synthetic broker-priority tests. No personal tokens, subscriptions or orders."""
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock

from research_monitor.broker_priority import (
    GrowwReadOnly, QuoteUnavailable, dual_quote_check, CHECK_UPSTOX_KEY,
)


NOW=datetime(2026,10,16,6,0,5,tzinfo=timezone.utc)
SECRET="synthetic-token-NEVER-SEND-0123456789"


class Response:
    status_code=200
    def json(self):
        return {"status":"SUCCESS","payload":{
            "last_price":1000.,"bid_price":999.,"offer_price":1001.,
            "last_trade_time":int((NOW-timedelta(seconds=3)).timestamp()*1000)
        }}


class FakeGroww:
    token=SECRET
    def __init__(self,price=1000., error=None, stale=False):
        self.price=price
        self.error=error
        self.stale=stale
        self.calls=[]
    def quote(self,symbol,now=None):
        self.calls.append(symbol)
        if self.error:
            raise QuoteUnavailable(self.error)
        return {
            "symbol":symbol,"ltp":self.price,
            "validation_errors":["STALE_OR_FUTURE_QUOTE"] if self.stale else [],
        }


class FakeUpstox:
    token=SECRET
    def __init__(self,price=1000.,error=None,stale=False):
        self.price=price
        self.error=error
        self.stale=stale
        self.calls=[]
    def quotes(self,keys,now):
        self.calls.append(keys)
        if self.error:
            from research_monitor.upstox_data import DataUnavailable
            raise DataUnavailable(self.error)
        return [{
            "token":CHECK_UPSTOX_KEY,"ltp":self.price,
            "validation_errors":["STALE_OR_FUTURE_QUOTE"] if self.stale else []
        }]


class PriorityTests(unittest.TestCase):
    def test_groww_primary_even_if_upstox_also_healthy(self):
        g,u=FakeGroww(),FakeUpstox()
        result=dual_quote_check(NOW,groww=g,upstox=u)
        self.assertEqual(g.calls,["RELIANCE"])
        self.assertEqual(u.calls,[[CHECK_UPSTOX_KEY]])
        self.assertEqual(result["chosen_data_provider"],"GROWW")
        self.assertFalse(result["automatic_broker_trade_fallback"])
        self.assertEqual(result["execution_preference"],"GROWW_ONLY")
        self.assertEqual(result["live_orders"],"DISABLED")
        self.assertFalse(result["trading_strategy_approved"])
        self.assertEqual(result["user_yes_no_order_execution"],"NOT_ENABLED")
        self.assertNotIn(SECRET,str(result))

    def test_upstox_data_only_if_groww_is_down(self):
        out=dual_quote_check(NOW,groww=FakeGroww(error="GROWW_HTTP_401"),
                             upstox=FakeUpstox())
        self.assertEqual(out["chosen_data_provider"],"UPSTOX")
        self.assertEqual(out["validation"],"FRESH_UPSTOX_FALLBACK_RESEARCH_ONLY")
        self.assertFalse(out["automatic_broker_trade_fallback"])
        self.assertFalse(out["live_eligible"])

    def test_out_of_sync_prices_fail_closed_even_with_two_fresh_feeds(self):
        out=dual_quote_check(NOW,groww=FakeGroww(1000.),
                             upstox=FakeUpstox(1050.))
        self.assertEqual(out["chosen_data_provider"],None)
        self.assertEqual(out["validation"],"CROSS_PROVIDER_PRICE_MISMATCH_BLOCKED")
        self.assertGreater(out["cross_provider_price_difference_pct"],1.)

    def test_both_stale_means_no_selection(self):
        out=dual_quote_check(NOW,groww=FakeGroww(stale=True),
                             upstox=FakeUpstox(stale=True))
        self.assertIsNone(out["chosen_data_provider"])
        self.assertEqual(out["validation"],"NO_FRESH_COMPARABLE_QUOTES")

    def test_http_get_only_and_bearer_not_in_error(self):
        called=[]
        def fake_get(url, **kwargs):
            called.append((url,kwargs))
            return Response()
        groww=GrowwReadOnly(token=SECRET,get=fake_get)
        q=groww.quote("RELIANCE",NOW)
        self.assertEqual(q["validation_errors"],[])
        self.assertEqual(len(called),1)
        self.assertTrue(called[0][0].endswith("/v1/live-data/quote"))
        self.assertNotIn("method",called[0][1])
        self.assertEqual(called[0][1]["params"]["segment"],"CASH")
        self.assertEqual(called[0][1]["headers"]["X-API-VERSION"],"1.0")
        self.assertEqual(q["source"],"GROWW")
        self.assertNotIn(SECRET,str(q))

    def test_missing_groww_token_and_live_upstox_only(self):
        class NoGroww:
            token=None
        out=dual_quote_check(NOW,groww=NoGroww(),upstox=FakeUpstox())
        self.assertEqual(out["groww"]["quote_status"],
                         "GROWW_SUBSCRIPTION_OR_TOKEN_MISSING")
        self.assertEqual(out["chosen_data_provider"],"UPSTOX")
        self.assertFalse(out["live_eligible"])

    def test_missing_two_tokens_never_calls_provider(self):
        class NoGroww:
            token=None
        class NoUpstox:
            token=None
        out=dual_quote_check(NOW,groww=NoGroww(),upstox=NoUpstox())
        self.assertEqual(out["validation"],"NO_FRESH_COMPARABLE_QUOTES")
        self.assertIsNone(out["chosen_data_provider"])
        self.assertEqual(out["live_orders"],"DISABLED")

    def test_invalid_groww_timestamp_or_quote_fails_closed(self):
        class StaleResponse:
            status_code=200
            def json(self):
                return {"status":"SUCCESS","payload":{
                    "last_price":1000.,"bid_price":999.,"offer_price":1001.,
                    "last_trade_time":"2026-10-15T13:00:00+05:30"
                }}
        g=GrowwReadOnly(token=SECRET,get=lambda *_a,**_k:StaleResponse())
        q=g.quote("RELIANCE",NOW)
        self.assertIn("STALE_OR_FUTURE_QUOTE",q["validation_errors"])


if __name__=="__main__":
    unittest.main()
