"""Read-only SmartAPI adapter. Caller supplies a valid session via environment.
No password/PIN handling, token logging or order endpoints.
"""
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import requests

IST = ZoneInfo('Asia/Kolkata')
BASE = 'https://apiconnect.angelone.in'
ALLOWED = {'quote': '/rest/secure/angelbroking/market/v1/quote/',
           'candles': '/rest/secure/angelbroking/historical/v1/getCandleData'}

class FeedUnavailable(RuntimeError):
    pass

class SmartAPIReadOnly:
    def __init__(self, session=None):
        required=['ANGEL_API_KEY','ANGEL_ACCESS_TOKEN','ANGEL_LOCAL_IP','ANGEL_PUBLIC_IP','ANGEL_MAC_ADDRESS']
        if any(not os.environ.get(k) for k in required):
            raise FeedUnavailable('SmartAPI session configuration missing; set server environment, never chat or GitHub')
        self.session=session or requests.Session()
        self.headers={'X-PrivateKey':os.environ['ANGEL_API_KEY'],
            'Authorization':'Bearer '+os.environ['ANGEL_ACCESS_TOKEN'].removeprefix('Bearer '),
            'X-ClientLocalIP':os.environ['ANGEL_LOCAL_IP'],'X-ClientPublicIP':os.environ['ANGEL_PUBLIC_IP'],
            'X-MACAddress':os.environ['ANGEL_MAC_ADDRESS'],'X-SourceID':'WEB','X-UserType':'USER',
            'Content-Type':'application/json','Accept':'application/json'}
        self.last_request=0.

    def _request(self, operation, payload):
        if operation not in ALLOWED:
            raise ValueError('Only quote and historical-data operations are permitted')
        wait=1.1-(time.monotonic()-self.last_request)
        if wait>0:
            time.sleep(wait)
        try:
            self.last_request=time.monotonic()
            response=self.session.post(BASE+ALLOWED[operation],json=payload,headers=self.headers,timeout=20,allow_redirects=False)
            if response.status_code != 200:
                raise FeedUnavailable(f'Market data HTTP {response.status_code}; signals blocked')
            data=response.json()
        except (requests.RequestException,ValueError) as exc:
            # Do not echo request objects, headers or provider response bodies.
            raise FeedUnavailable('Market data network/response failure; signals blocked') from None
        if data.get('status') is not True:
            raise FeedUnavailable('Provider session/data error; refresh credentials on server')
        return data.get('data')

    def quotes(self, exchange_tokens):
        count=sum(len(v) for v in exchange_tokens.values())
        if count<1 or count>50 or not set(exchange_tokens)<= {'NSE','NFO'}:
            raise ValueError('Use 1–50 NSE/NFO instrument tokens per request')
        data=self._request('quote',{'mode':'FULL','exchangeTokens':exchange_tokens})
        if not isinstance(data,dict) or data.get('unfetched'):
            raise FeedUnavailable('Incomplete quote batch; signals blocked')
        rows=data.get('fetched',[])
        expected={(ex,str(token)) for ex,tokens in exchange_tokens.items() for token in tokens}
        got={(r.get('exchange'),str(r.get('symbolToken'))) for r in rows}
        if got!=expected or len(rows)!=len(expected):
            raise FeedUnavailable('Quote tokens do not match request')
        return [normalize_quote(r) for r in rows]

    def candles(self, exchange, token, start, end, interval='ONE_DAY'):
        if exchange not in {'NSE','NFO'} or interval not in {'ONE_DAY','FIVE_MINUTE','FIFTEEN_MINUTE','ONE_HOUR'}:
            raise ValueError('Unsupported history request')
        return self._request('candles',{'exchange':exchange,'symboltoken':str(token),
            'interval':interval,'fromdate':start,'todate':end})


def normalize_quote(row):
    try:
        feed=datetime.strptime(row['exchFeedTime'],'%d-%b-%Y %H:%M:%S').replace(tzinfo=IST)
        depth=row.get('depth') or {}
        buys=[r for r in depth.get('buy',[]) if float(r['price'])>0 and float(r['quantity'])>0]
        sells=[r for r in depth.get('sell',[]) if float(r['price'])>0 and float(r['quantity'])>0]
        bid=max((float(r['price']) for r in buys),default=0.)
        ask=min((float(r['price']) for r in sells),default=0.)
        return dict(symbol=row['tradingSymbol'],token=str(row['symbolToken']),exchange=row['exchange'],
            ltp=float(row['ltp']),bid=bid,ask=ask,feed_time=feed.isoformat(),
            received_at=datetime.now(timezone.utc).isoformat(),source='Angel One FULL quote')
    except (KeyError,ValueError,TypeError):
        raise FeedUnavailable('Quote schema/timestamp invalid') from None
