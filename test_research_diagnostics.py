import csv
from pathlib import Path
import random
import tempfile
import unittest
from diagnose_research import snapshot
from research_monitor.backtest_gates import evidence_failures


class DiagnosticTests(unittest.TestCase):
    def test_gates_match_original_thresholds(self):
        rng = random.Random(18)
        for _ in range(1000):
            def sample():
                return dict(Completed_Trades=rng.choice([0,4,5,19,20,29,30,50]),
                            Mean_Return=rng.choice([-.01,0,.01]),
                            Profit_Factor=rng.choice([None,1.49,1.5,2]),
                            Max_Drawdown=rng.choice([.19,.20,.21]))
            dev, hold, stress, first, second = [sample() for _ in range(5)]
            expected = (dev['Completed_Trades']>=20 and dev['Mean_Return']>0
                and hold['Completed_Trades']>=30 and hold['Mean_Return']>0
                and hold['Profit_Factor'] is not None and hold['Profit_Factor']>=1.5
                and hold['Max_Drawdown']<=.20 and stress['Mean_Return']>0
                and first['Completed_Trades']>=5 and second['Completed_Trades']>=5
                and first['Mean_Return']>0 and second['Mean_Return']>0)
            self.assertEqual(not evidence_failures(dev,hold,stress,first,second), expected)

    def test_all_threshold_boundaries_and_individual_failures(self):
        good = dict(Completed_Trades=30, Mean_Return=.01, Profit_Factor=1.5, Max_Drawdown=.20)
        self.assertEqual(evidence_failures(*[good]*5), [])
        bad = dict(good, Completed_Trades=29)
        self.assertEqual(evidence_failures(good,bad,good,good,good), ['HOLDOUT_COMPLETED_TRADES_LT_30'])

    def test_old_report_is_not_misrepresented_as_exact_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/'swing_backtest_report.csv'
            path.write_text('Symbol,Backtest_Pass,Reason,Completed_Trades,Mean_Return\nABC,False,INSUFFICIENT_OR_WEAK_EVIDENCE,3,-0.01\nDEF,False,LESS_THAN_1260_BARS,,\n')
            before=path.read_bytes()
            result=snapshot(root)
            self.assertEqual(result['legacy_rows_without_full_failure_details'], 1)
            self.assertEqual(result['legacy_observable_failure_counts_overlapping']['HOLDOUT_COMPLETED_TRADES_LT_30'], 1)
            self.assertEqual(result['exact_failure_counts_overlapping'], {'LESS_THAN_1260_BARS':1})
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(len(list(root.iterdir())),1)

    def test_missing_report_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(snapshot(directory)['backtest_report_missing'])
