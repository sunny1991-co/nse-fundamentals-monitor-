"""MFA-protected candlestick chart display from locally stored read-only scans."""
from __future__ import annotations

from datetime import datetime,timezone
import json
from pathlib import Path

from flask import Blueprint,redirect,render_template_string,session,url_for

from research_monitor.live_chart_radar import STATUS

PAGE="""<!doctype html><html lang="hi"><meta charset="utf-8">
<meta name="viewport" content="width=device-width">
<meta http-equiv="refresh" content="60">
<title>NSE 1-Minute Chart Radar · Research</title><body><main>
<a href="/">← Dashboard</a>
<h1>Automatic 1-Minute Chart Analysis</h1>
<p><strong>Read-only market research, not buy/sell advice.</strong>
Only closed Upstox V3 1-minute candles are considered.
Refresh: approx. once a minute on valid live exchange sessions.
New company history and corporate-action adjustment remain separate.</p>
<p>Source status: {{ state }} · Checked: {{ checked_at }}
· Live candles verified: {{ live_ok }} · Orders: DISABLED</p>
{% if stale %}<p role="alert"><strong>Chart report stale or session closed.
Do not treat old charts as live signals.</strong></p>{% endif %}
{% for chart in charts %}
<section>
<h2>{{ chart.symbol }} — {{ chart.research_pattern }}</h2>
<p>Last closed minute: {{ chart.last_completed_minute }}</p>
<p>Close ₹{{ chart.close }} · EMA20 ₹{{ chart.ema20 }}
· EMA50 ₹{{ chart.ema50 }} · RSI14 {{ chart.rsi14 }}</p>
<p>MACD {{ chart.macd }} · Signal {{ chart.macd_signal }}
· VWAP ₹{{ chart.session_vwap }} · Volume Ratio {{ chart.last_volume_to_previous20_ratio }}</p>
<p>Previous 20-bar support ₹{{ chart.previous_20_bar_support }}
· Resistance ₹{{ chart.previous_20_bar_resistance }}</p>
<svg viewBox="0 0 900 270" width="100%" role="img"
 aria-label="Chart research: recent 1-minute completed candlesticks for {{ chart.symbol }}">
<rect x="0" y="0" width="900" height="270" fill="#111827"/>
{% for bar in chart.plot %}
<line x1="{{ bar.x }}" y1="{{ bar.yhigh }}" x2="{{ bar.x }}"
 y2="{{ bar.ylow }}" stroke="{{ bar.color }}" stroke-width="1.6"/>
<rect x="{{ bar.left }}" y="{{ bar.body_top }}" width="11"
 height="{{ bar.body_height }}" fill="{{ bar.color }}"/>
{% endfor %}
</svg>
<p>Green = closed higher; red = closed lower. Scale is relative to
recent plotted prices; this chart is not a full exchange tick feed.</p>
</section>
{% else %}
<p>No fresh, verified minute-close candlestick charts yet. This is
normal outside market hours, before enough intraday bars have closed,
or when broker API access/permissions are unavailable.</p>
{% endfor %}
{% if errors %}
<h2>Data availability review</h2>
{% for err in errors %}
<p>{{ err.symbol }}: {{ err.reason }}</p>
{% endfor %}
{% endif %}
<p>Patterns do NOT bypass 4-year audited NSE consolidated fundamentals,
corporate-action-adjusted EOD history, strategy holdout, forward paper
validation, or explicit owner's consent. No automatic real orders.</p>
</main></body></html>"""


def chart_plot(rows):
    rows=rows[-50:]
    if not rows:
        return []
    minimum=min(float(x["l"]) for x in rows)
    maximum=max(float(x["h"]) for x in rows)
    span=max(maximum-minimum,0.01)
    def y(value):
        return round(245-210*(float(value)-minimum)/span,2)
    plot=[]
    for idx,c in enumerate(rows):
        top=y(max(c["o"],c["c"]))
        bottom=y(min(c["o"],c["c"]))
        x=15+17*idx
        plot.append({
            "x":x,"left":x-5.5,
            "yhigh":y(c["h"]),"ylow":y(c["l"]),
            "body_top":top,"body_height":max(bottom-top,1.3),
            "color":"#34d399" if c["c"]>=c["o"] else "#fb7185",
        })
    return plot


def install(app,auth,root_getter):
    bp=Blueprint("live_charts",__name__,url_prefix="/charts")

    @bp.before_request
    def guarded():
        if not auth.valid(session.get("auth")):
            session.clear()
            return redirect(url_for("login"))

    @bp.get("")
    def show():
        root=Path(root_getter())
        try:
            value=json.loads((root/STATUS).read_text(encoding="utf-8"))
            if not isinstance(value,dict):
                raise ValueError("NOT_DICT")
        except (OSError,ValueError,TypeError):
            value={}
        now=datetime.now(timezone.utc)
        try:
            stamp=datetime.fromisoformat(value.get("checked_at_utc",""))
            stale=(stamp.tzinfo is None or not 0<=(now-stamp).total_seconds()<=180
                   or value.get("state")!="READ_ONLY_LIVE_CHART_RESEARCH")
        except ValueError:
            stale=True
        charts=[]
        if not stale:
            for data in value.get("charts",[])[:4]:
                if not isinstance(data,dict) or not data.get("research_only"):
                    continue
                row=dict(data)
                row["plot"]=chart_plot(data.get("chart_last_bars",[]))
                charts.append(row)
        return render_template_string(
            PAGE,state=value.get("state","NOT_STARTED"),
            checked_at=value.get("checked_at_utc",""),
            live_ok=not stale,charts=charts,stale=stale,
            errors=(value.get("symbol_review_errors") or [])[:4],
        )
    app.register_blueprint(bp)
