"""MFA/CSRF-protected manual paper rehearsal UI; no order or price-feed APIs."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from flask import Blueprint, abort, redirect, render_template_string, request, session, url_for
from research_monitor.paper_approval import PaperApprovals, BROKER_PREFERENCE

PAGE = '''<!doctype html><html lang="hi"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Paper research</title><body><main>
<a href="/">Dashboard</a><h1>Paper research — real orders disabled</h1>
<h2>Automatic EOD experiment</h2><p>{{ experiment }}</p>
<p>Teen hypotheses research-only hain. Naya eligible signal aur verified calendar/history ka wait ho sakta hai. Yeh capital form automatic experiment se alag rehearsal hai.</p>
{% if error %}<p role="alert">{{ error }}</p>{% endif %}
{% if proposal %}
<h2>Review exact paper plan</h2><dl>{% for key,value in proposal.plan.items() %}<dt>{{ key }}</dt><dd>{{ value }}</dd>{% endfor %}</dl>
<p>State: {{ proposal.state }} · Entry approval expires: {{ expiry }} UTC</p>
<p>Gap losses planned risk se zyada ho sakte hain. Fee estimate actual broker charges ka proof nahi hai.</p>
{% if proposal.state == 'PENDING' %}<form method="post" action="{{ url_for('paper_ui.decide', pid=proposal.id) }}">
<input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="digest" value="{{ proposal.digest }}">
<button name="answer" value="yes">Yes — paper only</button> <button name="answer" value="no">No</button></form>{% endif %}
<p>Yes real trade authorize nahi karta. Price observations CLI se aati hain; is form ka automatic broker feed connected nahi hai.</p>
<a href="{{ url_for('paper_ui.index') }}">New rehearsal</a>
{% else %}
<h2>Manual paper plan / capital</h2><p>Yeh stock recommendation nahi hai. Prices aur strategy version khud enter karke simulated plan review karein.</p>
<form method="post" action="{{ url_for('paper_ui.create') }}"><input type="hidden" name="csrf" value="{{ csrf }}">
<p><label>Symbol <input name="symbol" required maxlength="40"></label></p>
<p><label>Strategy version <input name="strategy" required maxlength="80"></label></p>
<p><label>Paper broker <select name="broker">{% for broker in brokers %}<option>{{ broker }}</option>{% endfor %}</select></label></p>
{% for field,label in fields %}<p><label>{{ label }} <input type="number" step="any" min="0.000001" name="{{ field }}" required></label></p>{% endfor %}
<button>Create plan for review</button></form>
<h2>Recent paper plans</h2><ul>{% for row in recent %}<li><a href="{{ url_for('paper_ui.show',pid=row.id) }}">{{ row.id[:8] }}</a> — {{ row.state }}</li>{% else %}<li>No plans yet.</li>{% endfor %}</ul>
{% endif %}</main></body></html>'''


def install(app, auth, root_getter):
    bp = Blueprint('paper_ui', __name__, url_prefix='/paper')

    @bp.before_request
    def authenticated():
        if not auth.valid(session.get('auth')):
            session.clear()
            return redirect(url_for('login'))
        # The application's before_request enforces CSRF on every POST.

    def ledger():
        return PaperApprovals(Path(root_getter())/'runtime'/'paper_approvals.sqlite3')

    def page(proposal=None, error='', status=200):
        root = Path(root_getter())
        experiment = 'Not enrolled / status unavailable'
        try:
            report = json.loads((root/'runtime'/'experiments'/'three-hypotheses-forward-v1'/'status.json').read_text())
            stamp = datetime.fromisoformat(report['checked_at'])
            age = (datetime.now(timezone.utc)-stamp).total_seconds()
            freshness = 'STALE' if not 0 <= age <= 48*3600 else 'LAST_RECORDED_CHECK'
            experiment = f"{freshness}: {report['checked_at']} · {report['state']} · Counts: {report.get('counts', {})} · {report.get('reason', '')}"
        except (OSError, ValueError, TypeError, KeyError):
            pass
        recent = []
        path = root/'runtime'/'paper_approvals.sqlite3'
        if path.exists():
            with sqlite3.connect('file:'+str(path.resolve())+'?mode=ro',uri=True) as db:
                recent = [dict(id=r[0],state=r[1]) for r in db.execute('SELECT id,state FROM proposals ORDER BY rowid DESC LIMIT 20')]
        expiry = datetime.fromtimestamp(proposal['expires'],timezone.utc).isoformat() if proposal else ''
        return render_template_string(PAGE, proposal=proposal, error=error,
            expiry=expiry, experiment=experiment, recent=recent,
            brokers=BROKER_PREFERENCE, csrf=session['csrf'], fields=[
                ('capital','Capital INR'),('entry_min','Entry minimum'),
                ('entry_max','Entry maximum'),('stop','Stop loss'),('target','Target'),
                ('fee_fraction','Estimated cost fraction per side (0.003 = 0.3%)')]),status

    @bp.get('')
    def index():
        return page()

    @bp.post('/create')
    def create():
        try:
            plan = ledger().create(**{k:request.form.get(k,'') for k in (
                'symbol','strategy','broker','capital','entry_min','entry_max','stop','target','fee_fraction')})
        except (ValueError, TypeError, OverflowError):
            return page(error='Invalid plan. Check capital, costs and stop < entry band < target.',status=400)
        return redirect(url_for('paper_ui.show',pid=plan['id']))

    @bp.get('/<pid>')
    def show(pid):
        try:
            row = ledger().get(pid)
        except ValueError:
            abort(404)
        return page(proposal=row)

    @bp.post('/<pid>/decide')
    def decide(pid):
        try:
            ledger().decide(pid,request.form.get('digest',''),request.form.get('answer',''))
        except ValueError:
            return page(error='Decision rejected: missing, changed, invalid or already decided plan.',status=409)
        return redirect(url_for('paper_ui.show',pid=pid))

    app.register_blueprint(bp)
