import unittest
import numpy as np
import pandas as pd
from swing_backtest import features, signal_conditions
from swing_signal_audit import filter_audit, entry_audit
from swing_strategy import ema, rsi, atr_adx


class FilterAuditTests(unittest.TestCase):
    def sample(self):
        rng=np.random.default_rng(21)
        close=100*np.exp(np.cumsum(rng.normal(.001,.025,1500)))
        return pd.DataFrame(dict(Date=pd.date_range('2020-01-01',periods=1500),
            Open=close, High=close*1.01, Low=close*.99, Close=close,
            Volume=rng.integers(100000,10000000,1500)))

    def test_refactor_preserves_original_signals_exactly(self):
        d=features(self.sample());c,v=d.Close,d.Volume
        e20,e50,e200=ema(c,20),ema(c,50),ema(c,200)
        macd=ema(c,12)-ema(c,26)
        original=((c>e20)&(e20>e50)&(e50>e200)&d.RSI.between(50,72)
            &(macd>ema(macd,9))&(d.ADX>=20)&(c>d.High.shift().rolling(20).max())
            &(v>=1.3*v.rolling(20).mean())&((c*v).rolling(20).mean()>=5e7)
            &(d.ATR>0)&((c-d.Stop)/c<=.12)&(d.Stop>0))
        pd.testing.assert_series_equal(d.Signal,original,check_names=False)

    def test_signal_funnel_accounts_for_every_holdout_day(self):
        d=features(self.sample());start=1116;end=len(d)
        counts=filter_audit(d,start,end)
        final=list(counts.values())[-1]['remaining_after']
        self.assertEqual(sum(x['first_rejected'] for x in counts.values())+final,end-start)
        self.assertEqual(final,entry_audit(d,start,end)['signal_opportunities'])

    def test_future_bars_do_not_change_past_indicators(self):
        raw=self.sample();short=features(raw.iloc[:900]);long=features(raw)
        pd.testing.assert_frame_equal(short,long.iloc[:900].reset_index(drop=True))

    def test_rsi_flat_rising_falling(self):
        self.assertEqual(rsi(pd.Series([100.]*60)).iloc[-1],50)
        self.assertEqual(rsi(pd.Series(range(1,61),dtype=float)).iloc[-1],100)
        self.assertEqual(rsi(pd.Series(range(60,0,-1),dtype=float)).iloc[-1],0)

    def test_atr_includes_overnight_gap(self):
        raw=pd.DataFrame(dict(High=[101.]*20+[111.],Low=[99.]*20+[109.],Close=[100.]*20+[110.]))
        atr,_=atr_adx(raw)
        self.assertAlmostEqual(atr.iloc[-1],2*(13/14)+11/14)
