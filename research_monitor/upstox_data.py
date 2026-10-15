"""Read-only Upstox data adapter; no order endpoint or trading credential flow."""
import os
from datetime import datetime, timezone
import requests
from research_monitor.schedule import IST
from research_monitor.risk import quote_errors

class DataUnavailable(RuntimeError):
    pass

class UpstoxData:
    def __init__(self, token=None, get=None):
        self.token = token or os.environ.get('UPSTOX_ANALYTICS_TOKEN')
        self.get = get or requests.get
    def _read(self, path, params=None):
        if not self.token:
            raise DataUnavailable('UPSTOX_TOKEN_REQUIRED')
        try:
            response = self.get('https://api.upstox.com/v2/'+path,
                headers={'Authorization':'Bearer '+self.token, 'Accept':'application/json'},
                params=params, timeout=10, allow_redirects=False)
            if response.status_code != 200:
                raise DataUnavailable('UPSTOX_HTTP_'+str(response.status_code))
            body = response.json()
            if body.get('status') != 'success':
                raise DataUnavailable('UPSTOX_RESPONSE_UNSUCCESSFUL')
            return body['data']
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
            raise DataUnavailable('UPSTOX_CONNECTION_OR_SCHEMA_ERROR') from None
    def calendar(self, now):
        day = now.astimezone(IST).date().isoformat()
        rows = self._read('market/timings/'+day)
        if not isinstance(rows,list):
            raise DataUnavailable('UPSTOX_TIMINGS_INVALID')
        sessions = {}
        try:
            nse = [r for r in rows if r['exchange']=='NSE']
            if len(nse)>1:
                raise ValueError('Multiple sessions require explicit handling')
            for row in nse:
                sessions[day] = dict(open=datetime.fromtimestamp(row['start_time']/1000,timezone.utc).isoformat(),
                    close=datetime.fromtimestamp(row['end_time']/1000,timezone.utc).isoformat())
        except (KeyError,TypeError,ValueError,OverflowError):
            raise DataUnavailable('UPSTOX_TIMINGS_INVALID') from None
        return dict(verified=True,source='Upstox market/timings',valid_from=day,valid_to=day,sessions=sessions)
    def is_open(self):
        data=self._read('market/status/NSE')
        return isinstance(data,dict) and data.get('exchange')=='NSE' and data.get('status')=='NORMAL_OPEN'
    def quotes(self, keys, now):
        if not isinstance(keys,list) or not keys or len(keys)>500 or any(not isinstance(k,str) or not k.startswith('NSE_EQ|') for k in keys):
            raise DataUnavailable('EQUITY_WATCHLIST_REQUIRED_MAX_500')
        raw=self._read('market-quote/quotes', {'instrument_key':','.join(keys)})
        rows=[]
        try:
            for item in raw.values():
                token=item['instrument_token']
                if token not in keys:
                    raise ValueError('Unexpected instrument')
                stamp=item['timestamp']
                if isinstance(stamp,(int,float)) or str(stamp).isdigit():
                    stamp=datetime.fromtimestamp(float(stamp)/1000,timezone.utc).isoformat()
                quote=dict(symbol=item['symbol'],token=token,exchange='NSE',feed_time=stamp,
                    bid=item['depth']['buy'][0]['price'],ask=item['depth']['sell'][0]['price'],ltp=item['last_price'])
                errors=quote_errors(quote,{'max_quote_age_seconds':15,'max_spread_fraction':.005},now)
                quote['validation_errors']=errors
                rows.append(quote)
            if {q['token'] for q in rows} != set(keys):
                raise ValueError('Missing instrument')
        except (KeyError,IndexError,TypeError,ValueError,AttributeError):
            raise DataUnavailable('UPSTOX_QUOTE_SCHEMA_OR_MAPPING_INVALID') from None
        return rows
