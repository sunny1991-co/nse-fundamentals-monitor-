# Automatic forward research: three frozen hypotheses

This adds automatic EOD paper observation for trend-following-v1,
ema-pullback-v1 and vcp-breakout-v1. They are competing technical hypotheses,
NOT three profitable or approved strategies. Existing historical results remain
negative/exploratory. Fundamental filters are not bypassed for trading: these
isolated observations are explicitly not fundamental-qualified or live eligible.

## Start on Termux

```bash
cd ~/nse-fundamentals-monitor-
git pull --ff-only
python -m unittest test_forward_experiment test_paper_forward test_paper_approval -q
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_paper_monitor.py
bash termux_backend.sh restart
```

The existing worker invokes this monitor in its premarket/after-market research
cycle, and the history worker invokes it after newly published history passes
its validation stages. No extra daemon is required. The backend must be running;
this is EOD observation, not an intraday broker stream or guaranteed uptime.
Calendar gaps or missing adjusted history block observation. Running the command
once enrolls the experiment even if data is currently unavailable.

Status:

```bash
cat ~/nse-fundamentals-data/runtime/experiments/three-hypotheses-forward-v1/status.json
```

No backfilled signals from before enrollment. Wait for a new close-confirmed
signal after enrollment. The history loader excludes current-day bars, so plans
normally register in the next premarket cycle. Registration after the next
verified opening is marked missed during reconciliation, never counted as a
successful forward trade. Daily calendars must cover intervening holidays too.
New empty counts are expected and do not prove failure or profitability.

Plans record the signal, exact prices, historical prefix hash and registration
time. The established simulator checks next-open eligibility, stop-first daily
ambiguity, gaps, partial targets and time exits. It simulates one normalized
unit per stock/strategy independently, with illustrative 0.2% costs and 0.1%
slippage each side, NOT actual broker fills, portfolio returns or fee quotes.
Each stock/strategy has at most one unresolved observation. Source revisions
block affected observations for review. A rules/engine code change blocks the
frozen experiment: use a reviewed new version rather than silently resetting it.

Historical comparisons are already available via
`python swing_seven_strategy_research.py --limit 100` with NSE_DATA_DIR set.
They are explicit offline experiments, not repeated daily optimization. The
existing standard backtest still runs on newly published history. New forward
observations never auto-promote a strategy or enable orders. Do not label any
candidate 'best' until independent evidence supports it after realistic costs.

This milestone does not add dashboard approval buttons, capital allocation to
this normalized-unit ledger, live feed execution, broker orders or SMS. The
separate capital/Yes-No rehearsal remains separate; approvals cannot authorize
these experimental observations to become real trades.
