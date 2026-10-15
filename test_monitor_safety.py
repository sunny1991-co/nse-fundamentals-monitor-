import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
import requests
import nse_sector_classification as sector
from swing_backtest import simulate
from swing_strategy import rsi

class SafetyTests(unittest.TestCase):
    def bars(self):
        rows=[dict(Date=pd.Timestamp('2020-01-01')+pd.Timedelta(days=i),Open=100.,High=101.,Low=99.,Close=100.,ATR=2.,Stop=95.,Signal=False) for i in range(224)]
        rows[219]['Signal']=True
        return pd.DataFrame(rows)

    def test_stop_precedes_target_on_ambiguous_bar(self):
        d=self.bars();d.loc[220,['High','Low']]=[120,90]
        _,t=simulate(d,220,224,cost=0,slip=0)
        self.assertEqual(t[0]['Reason'],'stop')
        self.assertAlmostEqual(t[0]['Return'],-.05)

    def test_gap_stop_uses_open(self):
        d=self.bars();d.loc[221,['Open','Low']]=[80,79]
        _,t=simulate(d,220,224,cost=0,slip=0)
        self.assertEqual(t[0]['Exit'],80)

    def test_no_same_bar_signal_entry(self):
        d=self.bars();d.loc[219,'Signal']=False;d.loc[223,'Signal']=True
        _,t=simulate(d,220,224)
        self.assertEqual(t,[])

    def test_costs_reduce_return(self):
        d=self.bars()
        a,_=simulate(d,220,224,cost=0,slip=0)
        b,_=simulate(d,220,224,cost=.002,slip=.001)
        self.assertLess(b['Total_Return'],a['Total_Return'])

    def test_partial_exit_accounting(self):
        d=self.bars();d.loc[220,'High']=111;d.loc[221,['Open','Low']]=[100,99]
        _,t=simulate(d,220,224,cost=0,slip=0)
        self.assertAlmostEqual(t[0]['Return'],.05)

    def test_entry_cap(self):
        d=self.bars();d.loc[220,'Open']=103
        _,t=simulate(d,220,224)
        self.assertEqual(t,[])

    def test_rsi_no_losses(self):
        self.assertEqual(rsi(pd.Series(range(40),dtype=float)).iloc[-1],100)

    def test_sector_block_records_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'map.csv'
            with patch.object(sector,'MAP_FILE',path),patch.object(sector,'candidate_symbols',return_value=['ABC']),patch.object(sector,'session_create',side_effect=requests.HTTPError('403')):
                sector.main()
            row=pd.read_csv(path).iloc[0]
            self.assertEqual(row.Lookup_Status,'ERROR')

    def test_fresh_sector_cache_does_not_connect(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'map.csv'
            pd.DataFrame([dict(Symbol='ABC',Lookup_Status='OK',Lookup_Date=sector.date.today().isoformat(),Sector='Industrials')]).to_csv(path,index=False)
            with patch.object(sector,'MAP_FILE',path),patch.object(sector,'candidate_symbols',return_value=['ABC']),patch.object(sector,'session_create') as connect:
                sector.main();connect.assert_not_called()

    def test_sector_session_block_does_not_quarantine_whole_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'map.csv'
            symbols = ['AAA', 'BBB', 'CCC', 'DDD', 'EEE']
            with (
                patch.object(sector, 'MAP_FILE', path),
                patch.object(sector, 'candidate_symbols', return_value=symbols),
                patch.object(sector, 'session_create', side_effect=requests.HTTPError('403')),
            ):
                sector.main()
            frame = pd.read_csv(path)
            self.assertLessEqual(len(frame), sector.MAX_CONSECUTIVE_BLOCKS)
            self.assertTrue(frame['Lookup_Status'].eq('ERROR').all())
            self.assertTrue({'CCC', 'DDD', 'EEE'}.isdisjoint(set(frame['Symbol'])))


if __name__=='__main__':
    unittest.main()
