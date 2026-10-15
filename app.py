"""Private research dashboard shell. No trading or broker order routes."""
import csv
import hmac
import os
import secrets
from pathlib import Path
from flask import Flask,abort,redirect,render_template_string,request,session,url_for
from research_monitor.auth import AuthStore

ROOT=Path(__file__).resolve().parent
LOGIN='''<!doctype html><html lang="hi"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Secure login</title><body><main><h1>NSE Research · सुरक्षित login</h1><p>Password और Authenticator code दोनों आवश्यक हैं।</p><form method="post"><input type="hidden" name="csrf" value="{{ csrf }}"><p><label>Password <input name="password" type="password" autocomplete="current-password" required maxlength="256"></label></p><p><label>Authenticator / recovery code <input name="code" autocomplete="one-time-code" required maxlength="80"></label></p><button>Login</button></form><p>{{ error }}</p></main></body></html>'''
DASH='''<!doctype html><html lang="hi"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>NSE Research</title><body><main><h1>NSE Research</h1><p>Research version · वास्तविक orders भेजने की सुविधा नहीं है।</p><h2>Swing</h2><p>Report date: {{ date }} · Fundamental shortlist: {{ count }}</p><p>Live feed और backtest validation अधूरे हों तो entry signal बंद रहता है।</p><h2>F&amp;O</h2><p>Paper/research only. Contract-level backtest और live-feed validation लंबित।</p><h2>IPO</h2><p>RHP research module. IPO feed connection लंबित। Pre-listing price backtest उपलब्ध नहीं।</p><form method="post" action="/logout"><input type="hidden" name="csrf" value="{{ csrf }}"><button>Logout</button></form></main></body></html>'''

def create_app(settings=None):
    cfg=dict(os.environ);cfg.update(settings or {})
    for key in ['APP_SECRET_KEY','APP_PASSWORD_HASH','APP_TOTP_SECRET','APP_AUTH_DB','APP_HOST']:
        if not cfg.get(key):
            raise RuntimeError('Security configuration incomplete; app startup refused')
    if len(cfg['APP_SECRET_KEY'])<32:
        raise RuntimeError('APP_SECRET_KEY must have at least 32 random characters')
    app=Flask(__name__)
    app.config.update(SECRET_KEY=cfg['APP_SECRET_KEY'],SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',SESSION_COOKIE_NAME='__Host-nse_session',
        MAX_CONTENT_LENGTH=4096,TRUSTED_HOSTS=[cfg['APP_HOST']])
    if cfg.get('TRUST_SINGLE_PROXY')=='1':
        # Enable only behind a known proxy that overwrites X-Forwarded-Proto.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app=ProxyFix(app.wsgi_app,x_for=0,x_proto=1,x_host=0,x_port=0,x_prefix=0)
    auth=AuthStore(cfg['APP_AUTH_DB'],cfg['APP_PASSWORD_HASH'],cfg['APP_TOTP_SECRET'])
    app.extensions['auth_store']=auth

    @app.before_request
    def guards():
        if not request.is_secure:
            abort(400)
        if request.method=='POST':
            supplied=request.form.get('csrf','');expected=session.get('csrf','')
            if not expected or not hmac.compare_digest(expected,supplied):
                abort(403)

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'no-store','Pragma':'no-cache',
            'Strict-Transport-Security':'max-age=31536000','X-Content-Type-Options':'nosniff',
            'X-Frame-Options':'DENY','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':"default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

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
        return render_template_string(DASH,count=count,date=day,csrf=session['csrf'])

    @app.post('/logout')
    def logout():
        auth.logout(session.get('auth',''));session.clear()
        return redirect(url_for('login'))
    return app
