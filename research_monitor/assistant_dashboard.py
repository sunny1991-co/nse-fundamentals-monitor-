"""MFA + CSRF-protected YES/NO + quantity screen for pending research proposals.

No user-created arbitrary stock order route, no quote-source spoofing,
no broker API, and no real-money execution. All entries/exits shown here
are *paper only* until separately validated live brokerage integration.
"""
from __future__ import annotations

from datetime import datetime,timezone
from pathlib import Path

from flask import (
    Blueprint, abort, redirect, render_template_string,
    request, session, url_for,
)

from research_monitor.assistant_trade_queue import ApprovalQueue

PAGE="""<!doctype html>
<html lang="hi"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>NSE Swing Assistant · Approval</title>
<body><main>
<a href="/">← Dashboard</a>
<h1>Swing assistant · Yes / No + Quantity</h1>
<p><strong>Research / paper mode only.</strong> Groww live orders are disabled.
Technical signals and backtests have not demonstrated independently
profitable trading. This is not a live buy recommendation.</p>
<p>The strategy decides hypothetical entry zone, stop-loss, targets and
100-session maximum holding. You only approve or reject the exact paper plan
and choose an integer quantity no greater than the displayed risk cap.</p>
{% if error %}<p role="alert">{{ error }}</p>{% endif %}
{% if current %}
<h2>{{ current.plan.symbol }} · {{ current.plan.strategy }}</h2>
<dl>
<dt>Signal day</dt><dd>{{ current.plan.signal_day }}</dd>
<dt>Hypothetical entry zone (₹)</dt>
<dd>{{ current.plan.entry_min }} — {{ current.plan.entry_max }}</dd>
<dt>Initial stop loss (₹)</dt><dd>{{ current.plan.stop_loss }}</dd>
<dt>Target 1 / Target 2 (₹)</dt>
<dd>{{ current.plan.target_1 }} / {{ current.plan.target_2 }}</dd>
<dt>Maximum risk-capped quantity</dt>
<dd>{{ current.plan.maximum_approved_quantity }}</dd>
<dt>Maximum modeled stop risk (₹)</dt>
<dd>{{ current.plan.max_initial_stop_risk_inr }}</dd>
<dt>Status</dt><dd>{{ current.state }}</dd>
<dt>Approved quantity</dt><dd>{{ current.quantity or 'Not approved' }}</dd>
<dt>Paper shares remaining</dt><dd>{{ current.shares_remaining or 0 }}</dd>
<dt>Approval expires (UTC)</dt><dd>{{ expiry }}</dd>
</dl>
<p>Gaps may exceed stop-loss and risk budget. Exact proposal values cannot
be changed or approved again after a decision.</p>
{% if current.state == "WAITING_FOR_OWNER" %}
<form method="post" action="{{ url_for('assistant_ui.decide',pid=current.id) }}">
<input type="hidden" name="csrf" value="{{ csrf }}">
<input type="hidden" name="fingerprint" value="{{ current.fingerprint }}">
<label>Quantity (whole NSE equity shares, max {{ current.plan.maximum_approved_quantity }})
<input type="number" name="quantity" min="1"
max="{{ current.plan.maximum_approved_quantity }}" step="1" required></label>
<p>
<button name="answer" value="yes">Yes · approve PAPER</button>
<button name="answer" value="no" formnovalidate>No</button>
</p></form>
{% endif %}
<a href="{{ url_for('assistant_ui.index') }}">See all proposals</a>
{% else %}
<h2>Strategy-discovered proposals</h2>
{% for row in recent %}
<p><a href="{{ url_for('assistant_ui.show',pid=row.id) }}">{{ row.plan.symbol }}</a>
— {{ row.state }}
— Entry ₹{{ row.plan.entry_min }}–{{ row.plan.entry_max }}
— Max qty {{ row.plan.maximum_approved_quantity }}</p>
{% else %}
<p>No eligible, fresh strategy-generated proposal yet. Required NIFTY TRI,
strict NSE audited fundamentals, fresh market feed and independent strategy
validation remain blockers for actionable/live signals.</p>
{% endfor %}
{% endif %}
<p>Order routing, protective CNC stop reconciliation and real-time tick
monitoring are NOT active. A YES here creates only a paper research approval.</p>
</main></body></html>"""


def install(app,auth,root_getter):
    bp=Blueprint("assistant_ui",__name__,url_prefix="/assistant")

    @bp.before_request
    def authenticated():
        if not auth.valid(session.get("auth")):
            session.clear()
            return redirect(url_for("login"))
        # App-wide before_request checks all POST CSRF tokens.

    def book():
        return ApprovalQueue(
            Path(root_getter())/"runtime"/"assistant_trade_queue.sqlite3"
        )

    def page(current=None,error="",status=200):
        expires=(
            datetime.fromtimestamp(current["expires"],timezone.utc).isoformat()
            if current else ""
        )
        rows=[] if current else book().list_recent()
        return render_template_string(
            PAGE,current=current,recent=rows,error=error,
            csrf=session["csrf"],expiry=expires,
        ),status

    @bp.get("")
    def index():
        return page()

    @bp.get("/<pid>")
    def show(pid):
        try:
            current=book().get(pid)
        except ValueError:
            abort(404)
        return page(current=current)

    @bp.post("/<pid>/decide")
    def decide(pid):
        try:
            book().decide(
                pid,request.form.get("fingerprint",""),
                request.form.get("answer",""),
                quantity=request.form.get("quantity",""),
            )
        except ValueError:
            try:
                current=book().get(pid)
            except ValueError:
                abort(404)
            return page(current=current,error=(
                "Approval rejected. Plan changed, expired, already decided,"
                " or quantity exceeds the conservative cap."
            ),status=409)
        return redirect(url_for("assistant_ui.show",pid=pid))

    app.register_blueprint(bp)
