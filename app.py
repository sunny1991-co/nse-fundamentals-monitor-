"""Private research dashboard shell. No trading or broker order routes."""
from research_monitor.paths import data_root
import csv
import json
from datetime import datetime, timezone
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path
from flask import Flask,abort,redirect,render_template_string,request,session,url_for
from research_monitor.auth import AuthStore
from research_monitor.readiness import candidate_rows
from research_monitor.paper_dashboard import install as install_paper_dashboard
from research_monitor.assistant_dashboard import install as install_assistant_dashboard
from research_monitor.live_chart_dashboard import install as install_live_chart_dashboard

ROOT=data_root()
LOGIN='''<!doctype html><html lang="hi"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Secure login</title><body><main><h1>NSE Research · सुरक्षित login</h1><p>Password और Authenticator code दोनों आवश्यक हैं।</p><form method="post"><input type="hidden" name="csrf" value="{{ csrf }}"><p><label>Password <input name="password" type="password" autocomplete="current-password" required maxlength="256"></label></p><p><label>Authenticator / recovery code <input name="code" autocomplete="one-time-code" required maxlength="80"></label></p><button>Login</button></form><p>{{ error }}</p></main></body></html>'''
DASH='''<!doctype html><html lang="hi"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Swing Research</title><body><main><h1>Swing Research</h1><p>Equity swing only · Orders aap manually broker app mein karenge.</p><h2>System status</h2><p>{{ worker }}</p><p>Live feed, approved SMS template aur forward paper validation pending hone par entry alerts blocked rahenge.</p><h2>Fundamental screening</h2><p>Report date: {{ date }} · Strict shortlist: {{ count }}</p><h2>Data readiness (last local check)</h2><p>{{ readiness }}</p><p>{{ history_recovery }}</p><h2>Conditional research plans</h2><p>{{ plan_status }}</p><p>Yeh executable entry alerts nahi hain. Historical win rate future probability nahi hai.</p><table><thead><tr><th>Stock</th><th>Signal date</th><th>Entry range</th><th>Stop</th><th>Targets at minimum entry</th><th>Evidence/status</th></tr></thead><tbody>{% for r in candidates %}<tr><td>{{ r.Symbol }}</td><td>{{ r.Signal_Date }}</td><td>{{ r.Entry_Min }} – {{ r.Entry_Max_No_Chase }}</td><td>{{ r.Stop_Loss }}</td><td>{{ r.Target_1_At_Min_Entry }} / {{ r.Target_2_At_Min_Entry }}</td><td>{{ r.Status }}</td></tr>{% else %}<tr><td colspan="6">Abhi verified actionable setup nahi hai.</td></tr>{% endfor %}</tbody></table><h2>Approval-based swing assistant</h2><p><a href="/assistant">Yes / No + Quantity — AI-inspired research signals (paper only)</a></p><p>Strict NSE fundamentals, market feed and forward validation missing hone par real orders BLOCKED hain.</p><h2>Automatic live chart analysis</h2><p><a href="/charts">1-minute candlestick chart: EMA, RSI, MACD, VWAP, breakout (read-only)</a></p><p>Verified NSE market session + authorized Upstox V3 1-minute candles required; no real orders.</p><h2>Paper research</h2><p><a href="/paper">Automatic experiment status / capital / paper Yes-No</a></p><p>{{ paper }}</p><p>Per-stock model observations only; no portfolio allocation or verified future win probability.</p><h2>Schedule</h2><p>Verified exchange calendar ke mutabik market session monitoring; close ke baad fundamental screening aur backtest. Holidays aur special sessions calendar se aayenge. Worker host ko chalu aur internet se connected rehna chahiye.</p><p>Capital abhi configure nahi hua ho to position sizing blocked rahegi. Stop loss ke paar gap hone par loss zyada ho sakta hai.</p><form method="post" action="/logout"><input type="hidden" name="csrf" value="{{ csrf }}"><button>Logout</button></form></main></body></html>''' 

def create_app(settings=None):
    cfg=dict(os.environ);cfg.update(settings or {})
    for key in ['APP_SECRET_KEY','APP_PASSWORD_HASH','APP_TOTP_SECRET','APP_AUTH_DB','APP_HOST']:
        if not cfg.get(key):
            raise RuntimeError('Security configuration incomplete; app startup refused')
    if len(cfg['APP_SECRET_KEY'])<32:
        raise RuntimeError('APP_SECRET_KEY must have at least 32 random characters')
    local_loopback = cfg.get('LOCAL_LOOPBACK_ONLY') == '1'
    if local_loopback:
        host = cfg['APP_HOST'].split(':', 1)[0].strip('[]').lower()
        if host not in {'localhost', '127.0.0.1', '::1'}:
            raise RuntimeError('LOCAL_LOOPBACK_ONLY requires localhost/loopback APP_HOST')
        if cfg.get('TRUST_SINGLE_PROXY') == '1':
            raise RuntimeError('Local loopback mode must not trust a proxy')
    app=Flask(__name__)
    app.config.update(SECRET_KEY=cfg['APP_SECRET_KEY'],SESSION_COOKIE_SECURE=not local_loopback,
        SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',
        SESSION_COOKIE_NAME='nse_local_session' if local_loopback else '__Host-nse_session',
        MAX_CONTENT_LENGTH=4096,TRUSTED_HOSTS=[cfg['APP_HOST']])
    if cfg.get('TRUST_SINGLE_PROXY')=='1':
        # Enable only behind a known proxy that overwrites X-Forwarded-Proto.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app=ProxyFix(app.wsgi_app,x_for=0,x_proto=1,x_host=0,x_port=0,x_prefix=0)
    auth=AuthStore(cfg['APP_AUTH_DB'],cfg['APP_PASSWORD_HASH'],cfg['APP_TOTP_SECRET'])
    app.extensions['auth_store']=auth
    install_paper_dashboard(app, auth, lambda: ROOT)
    install_assistant_dashboard(app, auth, lambda: ROOT)
    install_live_chart_dashboard(app, auth, lambda: ROOT)

    @app.before_request
    def guards():
        if request.path == '/healthz' and request.method in {'GET', 'HEAD'}:
            return None
        if not request.is_secure:
            if not local_loopback or request.remote_addr not in {'127.0.0.1', '::1'}:
                abort(400)
        if request.method=='POST':
            supplied=request.form.get('csrf','');expected=session.get('csrf','')
            if not expected or not hmac.compare_digest(expected,supplied):
                abort(403)

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'no-store','Pragma':'no-cache',
            'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':"default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
        if not local_loopback:
            response.headers['Strict-Transport-Security']='max-age=31536000'
        return response

    @app.get('/healthz')
    def health():
        # Public probe contains no account, portfolio, path or dependency details.
        try:
            with sqlite3.connect('file:'+str(Path(cfg['APP_AUTH_DB']).resolve())+'?mode=ro',
                                 uri=True, timeout=1) as db:
                db.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
            if cfg.get('CLOUD_SUPERVISED') == '1':
                state = json.loads((ROOT/'runtime'/'cloud_health.json').read_text())
                age = time.time()-state['updated_at']
                if state.get('processes_alive') is not True or not 0 <= age <= 15:
                    raise ValueError('Process health unavailable')
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            return {'status': 'unavailable'}, 503
        return {'status': 'ok'}, 200

    @app.route('/login',methods=['GET','POST'])
    def login():
        error=''
        session.setdefault('csrf',secrets.token_urlsafe(32))
        if request.method=='POST':
            token=auth.login(request.form.get('password',''),request.form.get('code','').strip())
            if token:
                session.clear();session['auth']=token;session['csrf']=secrets.token_urlsafe(32)
                return redirect(url_for('index'))
            error='Login नहीं हुआ। विवरण जाँचें; बार-बार असफल प्रयास पर 15 मिनट बाद प्रयास करें।'
        return render_template_string(LOGIN,csrf=session['csrf'],error=error)

    @app.get('/')
    def index():
        if not auth.valid(session.get('auth')):
            session.clear();return redirect(url_for('login'))
        count,day='असत्यापित','उपलब्ध नहीं'
        path=ROOT/'final_fundamental_screen_summary.csv'
        if path.exists():
            with path.open() as f:
                row=next(csv.DictReader(f),{})
                count=row.get('Final_Shortlist_Count',count);day=row.get('Report_Generated_On',day)
        worker='Worker not started / status unavailable'
        try:
            state=json.loads((ROOT/'runtime'/'worker_status.json').read_text(encoding='utf-8'))
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(state['updated_at'])).total_seconds()
            worker=(state['phase'] if 0<=age<=60 else 'WORKER STATUS STALE')+' · Live feed: '+state.get('live_feed','UNKNOWN')
        except (OSError,ValueError,KeyError,TypeError):
            pass
        candidates, plan_status = candidate_rows(ROOT)
        history_recovery = 'Official NSE adjusted-history recovery: not started.'
        try:
            recovery = json.loads((ROOT/'runtime'/'nse_eod_history_status.json').read_text(encoding='utf-8'))
            daily = recovery.get('daily') or {}
            actions = recovery.get('corporate_actions') or {}
            action_audit = recovery.get('corporate_action_audit') or {}
            gap_audit = recovery.get('archive_gap_audit') or {}
            history_recovery = (
                'Official NSE adjusted-history recovery: '+str(recovery.get('state','UNKNOWN'))
                +' · Remaining archive days: '+str(daily.get('remaining_archive_days',daily.get('remaining_weekdays','?')))
                +' · Unverified missing archives: '+str(gap_audit.get('unverified_expected_session_gaps',daily.get('unverified_no_file_days',0)))
                +' · Exhausted archive retries: '+str(gap_audit.get('retry_exhausted_gaps',0))
                +' · Corporate actions: '+str(actions.get('status','UNKNOWN'))
                +' · Action-review symbols: '+str(action_audit.get('unsupported_symbols',0))
                +' · Upstox: '+str(recovery.get('upstox','NOT_USED'))
            )
        except (OSError, ValueError, TypeError):
            pass
        readiness = 'Run python swing_readiness.py for local data/readiness checks.'
        try:
            report = json.loads((ROOT/'runtime'/'readiness.json').read_text(encoding='utf-8'))
            readiness = 'Checked: '+report['updated_at']+' · History: '+report['historical_data']+' · Backtest: '+report['backtest']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        paper='Paper ledger not started; no real orders.'
        try:
            snapshot=json.loads((ROOT/'runtime'/'paper_status.json').read_text(encoding='utf-8'))
            paper='Checked: '+snapshot['checked_at']+' · '+snapshot.get('data_status','UNKNOWN')+' · '+str(snapshot.get('counts',{}))
        except (OSError,ValueError,KeyError,TypeError):
            pass
        return render_template_string(DASH,paper=paper,count=count,date=day,worker=worker,candidates=candidates,plan_status=plan_status,readiness=readiness,history_recovery=history_recovery,csrf=session['csrf'])

    @app.post('/logout')
    def logout():
        auth.logout(session.get('auth',''));session.clear()
        return redirect(url_for('login'))
    return app

