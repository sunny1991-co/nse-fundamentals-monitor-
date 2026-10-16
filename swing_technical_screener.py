"""Publish only current fundamental-pass AND technical holdout-pass candidates."""
import argparse
from research_monitor.paths import data_root
from pathlib import Path
import pandas as pd
from research_monitor.readiness import candidate_manifest
from research_monitor.schedule import atomic_status
from swing_backtest import main as run_backtests, load_market, features, VERSION, MAX_HOLD
ROOT = data_root()
OUTPUT = ROOT/'swing_trade_candidates.csv'

def current_backtest_matches(raw, digest):
    path = ROOT/'swing_backtest_report.csv'
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        report = pd.read_csv(path, dtype=str, keep_default_na=False)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return False
    if report.empty or 'Symbol' not in report or 'Backtest_Pass' not in report:
        return False
    expected = set(raw.Symbol.astype(str))
    if set(report.Symbol.astype(str)) != expected or report.Symbol.duplicated().any():
        return False
    for _, row in report.iterrows():
        if str(row.get('Reason','')) == 'LESS_THAN_1260_BARS':
            continue
        if row.get('Data_SHA256') != digest or row.get('Strategy') != VERSION:
            return False
    return True

def main(reuse_current_backtest=False):
    OUTPUT.unlink(missing_ok=True)
    (ROOT/'swing_candidates_manifest.json').unlink(missing_ok=True)
    required_history = (ROOT/'market_ohlcv.csv', ROOT/'market_data_metadata.json')
    if not all(path.is_file() for path in required_history):
        print('WAITING_FOR_VALID_ADJUSTED_HISTORY; no technical candidates published')
        return
    if reuse_current_backtest:
        raw, digest = load_market()
        if not current_backtest_matches(raw, digest):
            run_backtests()
            raw, digest = load_market()
    else:
        # Preserve fail-closed default behavior: force a fresh internally
        # generated report before trusting any market-data read.
        run_backtests()
        raw, digest = load_market()
    approved = pd.read_csv(ROOT/'final_fundamental_shortlist.csv', dtype=str)
    if not {'Symbol','Final_Fundamental_Criteria_Pass'}.issubset(approved):
        raise ValueError('Verified final fundamental flags required')
    summary = pd.read_csv(ROOT/'final_fundamental_screen_summary.csv', dtype=str)
    today = pd.Timestamp.now(tz='Asia/Kolkata').tz_localize(None).normalize()
    stamp = pd.to_datetime(summary.Report_Generated_On, errors='raise').min()
    if pd.isna(stamp) or not 0 <= (today-stamp).days <= 1:
        raise ValueError('Fundamental report requires fresh verification')
    symbols = set(approved.loc[approved.Final_Fundamental_Criteria_Pass.str.lower().eq('true'),'Symbol'])
    report = pd.read_csv(ROOT/'swing_backtest_report.csv')
    rows=[]
    for sym, bars in raw.groupby('Symbol'):
        evidence=report[report.Symbol.eq(sym)]
        if sym not in symbols or evidence.empty:
            continue
        e=evidence.iloc[0]
        if str(e.Backtest_Pass).lower()!='true' or e.get('Data_SHA256')!=digest:
            continue
        d=features(bars); b=d.iloc[-1]
        if not bool(b.Signal) or not 0 <= (today-b.Date).days <= 4:
            continue
        entry=float(b.Close); cap=entry*1.01; stop=float(b.Stop)
        rows.append(dict(Symbol=sym, Signal_Date=str(b.Date.date()), Strategy=VERSION,
            Entry_Min=round(entry,2), Entry_Max_No_Chase=round(cap,2), Stop_Loss=round(stop,2),
            Entry_Rule='Next trading-session open only; skip outside range incl slippage; expires that session',
            Target_1_At_Min_Entry=round(entry+2*(entry-stop),2),
            Target_2_At_Min_Entry=round(entry+3*(entry-stop),2),
            Target_Rule='Recompute from actual fill: sell 50% at fill+2R; rest at fill+3R; R=fill-stop',
            Trailing_Rule='After T1: next-session stop=max(previous stop, entry, completed close-2ATR)',
            Time_Exit_Sessions=MAX_HOLD, Holdout_Trades=e.Trades, Win_Rate=e.Win_Rate,
            Mean_Net_Trade_Return=e.Mean_Return, Max_Drawdown=e.Max_Drawdown,
            Backtest_Start=e.Start, Holdout_Start=e.Holdout_Start, Backtest_End=e.End,
            Status='EOD conditional plan; technical holdout evidence; live price not connected'))
    pd.DataFrame(rows,columns=None if rows else ['Symbol','Signal_Date','Entry_Min','Entry_Max_No_Chase','Stop_Loss','Status']).to_csv(OUTPUT,index=False)
    atomic_status(ROOT/'swing_candidates_manifest.json', candidate_manifest(ROOT))
    print(f'{len(rows)} evidence-gated candidates; no future win probability claimed')

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reuse-current-backtest', action='store_true')
    args = parser.parse_args()
    main(reuse_current_backtest=args.reuse_current_backtest)

