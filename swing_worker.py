"""Swing supervisor for a trusted local host. No broker orders are placed."""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Load private local settings before data_root() is resolved. This matters on
# Termux where NSE_DATA_DIR may be set only in the ignored .env file.
from dotenv import load_dotenv
_CODE_DIR = Path(__file__).resolve().parent
load_dotenv(_CODE_DIR/'.env', interpolate=False)

from research_monitor.paths import data_root, CODE_ROOT
from research_monitor.upstox_data import UpstoxData, DataUnavailable
from research_monitor.schedule import IST, phase, atomic_status, claim_job
from research_monitor.nse_calendar import official_equity_calendar

ROOT = data_root()

def tick(root=ROOT, now=None):
    now = now or datetime.now(timezone.utc)
    runtime = root/'runtime'
    runtime.mkdir(exist_ok=True)
    try:
        calendar = json.loads((root/'exchange_sessions.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        calendar = {}
    provider=UpstoxData()
    if phase(calendar,now)=='CALENDAR_PENDING' and os.environ.get('UPSTOX_ANALYTICS_TOKEN'):
        try:
            calendar=provider.calendar(now)
            atomic_status(root/'exchange_sessions.json',calendar)
        except DataUnavailable:
            calendar={}
    state = phase(calendar, now)
    if state == 'CALENDAR_PENDING' and not os.environ.get('UPSTOX_ANALYTICS_TOKEN'):
        # Broker-independent fallback: only reviewed official NSE calendar
        # snapshots are accepted. Unsupported years remain fail-closed.
        calendar = official_equity_calendar(now.astimezone(IST).year)
        atomic_status(root/'exchange_sessions.json', calendar)
        state = phase(calendar, now)
    if state != 'CALENDAR_PENDING':
        history_path=runtime/'calendar_history.json'
        try:
            history=json.loads(history_path.read_text(encoding='utf-8'))
            if not isinstance(history,dict): history={}
        except (OSError,ValueError):
            history={}
        day=now.astimezone(IST).date().isoformat()
        if history.get(day) != calendar:
            history[day]=calendar
            atomic_status(history_path,history)
    status = dict(updated_at=now.isoformat(), phase=state, scope='SWING_EQUITY_ONLY',
        live_feed='NOT_CONNECTED', sms='PROVIDER_SETUP_REQUIRED',
        entry_alerts='BLOCKED_PENDING_LIVE_AND_FORWARD_VALIDATION',
        paper_trading='PENDING_VALIDATED_FEED', orders='MANUAL_ONLY')
    if state=='LIVE_MONITORING':
        (runtime/'quotes.json').unlink(missing_ok=True)
        try:
            if not provider.is_open():
                status['live_feed']='EXCHANGE_NOT_NORMAL_OPEN'
            else:
                watchlist=json.loads((root/'swing_watchlist.json').read_text(encoding='utf-8'))
                quotes=provider.quotes(watchlist,datetime.now(timezone.utc))
                atomic_status(runtime/'quotes.json',dict(fetched_at=now.isoformat(),quotes=quotes))
                status['live_feed']='POLLING_10_SECONDS' if all(not q['validation_errors'] for q in quotes) else 'QUOTE_VALIDATION_BLOCKED'
        except DataUnavailable as exc:
            status['live_feed']=str(exc)
        except (OSError,ValueError,TypeError):
            status['live_feed']='VERIFIED_EQUITY_WATCHLIST_REQUIRED'
    # After-close jobs run even if the laptop starts after the close.
    # Non-trading days can refresh research, but can never open a live session.
    if state in {'PRE_MARKET', 'AFTER_MARKET_ANALYSIS', 'NON_TRADING_DAY', 'CALENDAR_PENDING'}:
        day = now.astimezone(IST).date().isoformat()
        job=('offline_research' if state=='CALENDAR_PENDING' else
             'premarket_research' if state=='PRE_MARKET' else 'eod_research')
        if os.environ.get('NSE_BOOT_ID'):
            job += ':'+os.environ['NSE_BOOT_ID']
        if claim_job(runtime/'schedule.sqlite', day, job):
            status['analysis'] = 'RUNNING'
            atomic_status(runtime/'worker_status.json', status)
            failed = None
            for script in ['annual_fundamental_quality.py', 'final_fundamental_shortlist.py', 'swing_technical_screener.py']:
                try:
                    with (runtime/'analysis.log').open('a', encoding='utf-8') as log:
                        log.write('\n'+now.isoformat()+' '+script+'\n'); log.flush()
                        result = subprocess.run([sys.executable, str(CODE_ROOT/script)], cwd=root,
                            stdout=log, stderr=log, timeout=1800, check=False)
                    if result.returncode:
                        failed = script
                        break
                except (OSError, subprocess.TimeoutExpired):
                    failed = script
                    break
            # Existing paper observations must still advance if a fresh screen fails.
            for script in ['swing_paper_monitor.py','swing_readiness.py']:
                try:
                    with (runtime/'analysis.log').open('a',encoding='utf-8') as log:
                        result=subprocess.run([sys.executable,str(CODE_ROOT/script)],cwd=root,
                            stdout=log,stderr=log,timeout=1800,check=False)
                    if result.returncode: failed=failed or script
                except (OSError,subprocess.TimeoutExpired):
                    failed=failed or script
            status['analysis'] = 'BLOCKED_CHECK_ANALYSIS_LOG' if failed else 'COMPLETED' 
            status['failed_step'] = failed
            atomic_status(runtime/'last_analysis.json', status)
    try:
        paper=json.loads((runtime/'paper_status.json').read_text(encoding='utf-8'))
        status['paper_trading']=paper.get('data_status','STATUS_UNAVAILABLE')
        status['paper_checked_at']=paper.get('checked_at')
    except (OSError,ValueError,TypeError):
        pass
    atomic_status(runtime/'worker_status.json', status)
    return status

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    # SQLite lease held for process lifetime prevents duplicate workers.
    import sqlite3
    (ROOT/'runtime').mkdir(exist_ok=True)
    lock = sqlite3.connect(ROOT/'runtime'/'worker_lock.sqlite', timeout=1)
    try:
        lock.execute('BEGIN EXCLUSIVE')
    except sqlite3.OperationalError:
        raise SystemExit('Swing worker already running.')
    try:
        while True:
            print(json.dumps(tick()), flush=True)
            if args.once:
                break
            time.sleep(10)
    except KeyboardInterrupt:
        pass
    finally:
        lock.rollback(); lock.close()

if __name__ == '__main__':
    main()

