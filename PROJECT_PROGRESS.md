# Project progress — 16 October 2026

## Termux restart and real Upstox token status — 16 October 2026

Owner's actual phone-local configuration (no secrets shared):
- `upstox_token_setup.py` stored a READ-ONLY Analytics token locally, permissions 0600.
  Real `market/status/NSE` GET returned OK and `CLOSING_END`;
  known RELIANCE `market-quote/quotes` GET returned an expected schema.
  This is **endpoint authentication evidence**, NOT verified fresh live quote.
- `swing_broker_priority_check.py` confirmed Groww token **missing** and
  Upstox token **present**; after session close its quote was rejected for
  stale timestamp and invalid bid/ask. Therefore no chosen fresh provider.
- `termux_backend.sh restart` succeeded; supervisor, swing, NSE history,
  research and dashboard processes are RUNNING, wake lock TRUE, dashboard
  `http://localhost:8787` loopback MFA. Worker phase after-market, no live
  connection or automatic orders. Historical adjusted data published, 660
  old-history research eligible symbols, 222 corporate-action review symbols.
- **Major blockers remain:** annual strict PASS 0; official sector rows 0;
  quarterly collection bounded attempts; final shortlist 0; validated
  trading strategy 0; SMS provider not configured. Fast fundamental
  provisional 478 is LOWER CONFIDENCE and NOT for trading.
- To prevent misleading interpretation of hard-coded
  `upstox: INTENTIONALLY_DEFERRED` from old no-Upstox NSE archive maintenance,
  `swing_worker` now records local broker token presence separately from
  session/quote validation. `project_status.py` reports
  `upstox_token_status=CONFIGURED_READ_ONLY_NOT_LIVE_VERIFIED` when the
  worker confirms local configuration, and still restricts live orders.
  For a valid closed session status is
  `SESSION_NOT_OPEN_NO_LIVE_QUOTE`; unknown calendars stay
  `NOT_CONNECTED`, fail-closed.

Next priority: diagnose annual/sector/quarterly strict-pass blockers rather
than promote an unvalidated trading strategy or pay for Groww API immediately.
Run `NSE_DATA_DIR="$HOME/nse-fundamentals-data" python diagnose_fundamental_blockers.py`
on the local private datasets. Do not ask for the token or publish private
OHLCV/credential files.

## Broker source priority frozen — 16 October 2026

Owner requested **Groww first, Upstox second** for all future swing workflows,
including the goal of human Yes/No approval before any Groww delivery trade.
Official documentation confirms Groww paid Trading APIs support CASH data
and order management, currently ₹499 + applicable taxes/month, with
short-lived access tokens. Upstox Analytics Token is **read-only** and cannot
place/modify/cancel trades; only data backup is possible on this token.

Added `research_monitor/broker_priority.py` and
`swing_broker_priority_check.py` for explicit **read-only** checks of a
single documented NSE equity (RELIANCE) from both brokers. If both provide
fresh normalized quotes, Groww wins. If Groww is unavailable and Upstox
is fresh, Upstox can serve as **data backup only**. If valid fresh prices
diverge by more than 1%, source selection is blocked. Closed-market
stale quotes are not represented as live.

Added hidden private-token `groww_token_setup.py` preserving the already
stored Upstox token, with chmod 0600 local `.env`. Relevant synthetic
unit tests demonstrate no credential echo, no broker POST endpoints,
strict quote freshness, no silent trading fallback and no strategy
approval. Results remain **NOT LIVE**; files have not yet been executed
with actual account tokens in the owner's Termux environment. The existing
swing worker is not yet switched to a verified provider-mapped stock
watchlist by this one-stock reference health check.

**CNC swing automation blocker:** Groww official Smart OCO currently
documents `CASH/MIS` support, not `CASH/CNC`; a 1-week–5-month delivery
position cannot safely rely on unsupported OCO protection. Any future
entry/stop/exit automation must first verify CNC-valid broker protection,
reconcile actual partial fills, define offline/overnight failure handling,
and require per-proposal user approval and an independently validated
strategy. Do not spend on API subscription just to rerun the seven
negative strategies. **Zero live orders, zero approved profitable
strategies.** See `BROKER_PRIORITY.md`.

```bash
cd ~/nse-fundamentals-monitor- && git pull
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_broker_priority_check.py
```

## Regime + Momentum private research outcome — 16 October 2026

Owner ran `swing_regime_momentum_research.py --limit 100` on adjusted
Termux historical market data. Eligible universe **660**, tested
**100** hash-sampled stocks. All runs use theoretical 1% initial-stop
capital risk, estimated Groww delivery charges, whole shares and the frozen
research window. **New strategy status: REJECTED FOR TRADING;
UNVALIDATED exploratory evidence only.**

| Measure | Regime Momentum new hypothesis | VCP comparator |
|---|---:|---:|
| Signals | 5 | 46 |
| Completed trades (normal) | 3 | 19 |
| Wins / losses | 0 / 3 | 5 / 14 |
| Mean net position return, completed | -9.6177% | -2.1160% |
| Mean net pre-trade stock-account cash return, completed | -0.9641% | -0.2191% |
| Net completed profit factor | 0 | 0.6842 |
| Worst *independent-stock* marked drawdown | 1.9094% | 2.0622% |
| Normal scenario estimated charges summed across stock-account experiments | INR 203.98 | INR 1,727.68 |
| Double-cost completed trades / wins | 2 / 0 | 16 / 4 |
| Double-cost mean net position return | -10.0332% | -2.7720% |

This is a small and losing selected sample, *not* a definitive future
prediction. Historical point-in-time fundamentals were **not** applied,
historic universe composition remains survivor-biased and fees are estimates.
No rule tuning or gate loosening is authorized from these results. Keep
reproducible historical reports but do not schedule repeated frozen-window
runs. No automated trade approvals, SMS entry alerts or orders.

**Next priorities:** obtain verified point-in-time fundamental and
historical universe inputs, audit history and corporate-action flags,
pre-register a *different* prospectively tested methodology if warranted,
then collect new post-freeze paper observations with independently
verified real broker costs and portfolio-level risk constraints. Do
not mistake synthetic code-test success or one positive historical
subgroup for profitability.

## Original Regime + Momentum Confirmation v1 — research only (16 October 2026)

**Frozen before running the new strategy on private NSE history.** Added
`research_monitor/regime_momentum_rules.py`,
`swing_regime_momentum_research.py`, synthetic regression tests and
`REGIME_MOMENTUM_STRATEGY.md`.

This one original hypothesis combines >=55% equal-weight price breadth across
the history-eligible universe, >=70th-percentile 126-session relative momentum
with >10% 126-session absolute price return, rising EMA50/EMA200 trend, close
confirmation through the *previous* 15-day high, volume/traded-value
qualification, RSI/ATR guardrails, earlier 20/60-day volatility confirmation,
existing <=10%-distance stop and **1% fee-inclusive no-gap capital sizing**.
A missing breadth/rank date blocks signals. Reuses unchanged stop-first 2R/3R
exit structure and next-session guarded entries.

The private research runner compares this strategy with the already-negative
VCP breakout **on the same deterministic 100-history-symbol sample**, with
normal estimated Groww delivery charges and double-cost/slippage stress;
all stock ledgers are *independent*, not a joint portfolio. Context breadth and
rank are computed across all 1260-bar eligible symbols, which avoids using
only the sampled 100 for those context calculations, but **does NOT solve
historical survivor bias**, fundamentally missing point-in-time financials,
or prior inspection of the very same historical price window. No result,
win-rate or profitability has been claimed until private Termux runs.

```bash
cd ~/nse-fundamentals-monitor- && git pull
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_regime_momentum_research.py --limit 100
```

No candidate alerts, live trades or broker orders are connected. Existing
rejected experiments remain rejected; the new entry is also blocked from
promotion until clean independent evaluation, point-in-time data and genuine
forward paper-trading prove reproducible results net of actual costs.

## Owner-tested 1% risk exposure results — 16 October 2026

Real private phone run: 660 symbols with sufficient history and the same frozen
100-stock experiment. Hypothetical single-stock capital INR 100,000 each.
These are **not portfolio returns**. Figures below are owner-supplied
completed-trade averages normalized by *pre-trade stock-account cash*; the
drawdowns are the **worst independent stock** in the same fixed sample.

| Previously unprofitable hypothesis | Full-cash mean capital return per completed trade | Fixed 1%-risk mean | Worst individual-stock DD full cash → fixed 1% | Risk-budget breaches (fixed 1%; double-cost) |
|---|---:|---:|---:|---:|
| VCP breakout | -0.911% | -0.219% | 19.918% → 2.062% | 0; 1 |
| Mean reversion | -1.649% | -0.241% | 42.519% → 5.055% | 4; 5 |
| Custom dip reclaim | -1.759% | -0.373% | 25.169% → 4.015% | 1; 2 |

The apparent reduction is driven primarily by smaller capital at risk (median
deployed fraction: 9.338%, 12.005% and 12.486% respectively); **the
underlying signal expectancy is still negative**. Actual exit prices and gap
moves sometimes exceeded the hypothetical 1% initial-stop loss budget, even
with correct fee-aware share sizing. Double-cost stress means remained
negative. No winning strategy has been discovered and no live trading is
permitted by this experiment.

**Research disposition:** keep the original seven and custom hypotheses
as failed retrospective attempts, retain frozen private historical records,
and do not test endless loss-making parameter variations on the same already
inspected sample. Prioritize verified primary-source multi-year fundamentals,
corporate-action and dated market-history checks, a correctly dated universe,
then preregistered new entry hypotheses and genuinely fresh forward/independent
paper observations. Strict final fundamental screening remains blocked by
missing verified coverage and must not be loosened to create a positive
backtest. A lower drawdown alone does not establish trading merit.

## Fixed 1% stop-risk research — October 16, 2026

The owner's private phone output showed heavy initial-stop exits on the inspected
100-stock sample: VCP **13 initial-stop exits averaging -9.358%** versus
2 trailing-stop exits averaging +15.701%; mean reversion **76 initial**
averaging -8.067% versus 14 trailing averaging +9.244%; custom dip-reclaim
**33 initial** averaging -6.863% versus 5 trailing averaging +8.822%.
These are **conditional exit subsets**, not evidence that trailing-stop rules
cause profits. Owner output also showed nonflagged means remain negative:
VCP -0.384% across 19 completed, mean reversion -1.108% across 99,
custom -1.772% across 44. Fees were about 0.30 percentage points per
completed trade. Forensic groups and flagged historical jumps do not justify
retrospective exclusions or relaxed stops.

Added an **opt-in** `risk_fraction` to the independent single-stock
`simulate_delivery` model (unchanged default). For a fixed 1% scenario it
uses integer shares, the initial stop, estimated exit slippage, and *buy and
sell fees* to limit **projected, no-gap** loss to 1% of cash before the trade.
It does not alter strategy signals, stop prices, trailing rules or order
handling. **An actual overnight gap, market halt or real fill can exceed 1%**.
Untradeably small positions are skipped rather than bypassing fees/risk.

The new `swing_risk_sizing_research.py` compares the unchanged all-cash
execution with fixed 1% position risk and doubled costs/slippage for
VCP, mean-reversion and custom-dip on identical symbol/date windows.
Those three were selected *after* previous performance was inspected;
comparison is only about exposure, not validating their expected returns.
Runs are manual, never automatic or live, and per-stock INR 100k simulated
ledgers do **not** form a shared diversified portfolio. Code and input
hashes are retained.

```bash
cd ~/nse-fundamentals-monitor- && git pull
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_risk_sizing_research.py --limit 100
```

GitHub synthetic tests do not measure the user's real stock returns.
No strategy approval, candidate promotion or live order permission is added.
Independent forward tests and actual broker contract notes remain necessary.

## Strategy research pause and loss forensics — 16 October 2026

The owner ran private adjusted NSE history with **100 historical symbols** in the
exploratory Groww-fee scenario. All **seven** distinct price-only hypotheses
had negative pooled completed-trade mean returns. The best retrospective
mean was **VCP breakout -0.91% (20 completed trades)**; mean reversion was
**-1.64% (105)**, custom dip-reclaim **-1.77% (44)**, trend-pullback
**-2.85% (89)**, trend-following **-4.55% (62)**, momentum breakout
**-5.38% (46)**, breakout **-7.23% (14)**. These are owner-supplied output
figures, per trade and not portfolio returns. Regulatory/broker charges were
estimated from the published Groww NSE delivery schedule; personal taxes,
historical brokerage tariff changes and real fills were not verified.
**Zero verified profitable strategies; no live orders.**

Fixed retrospective experiments terminate their in-sample exploration on
2026-10-15. The three redundant scheduled tests were removed from
`termux_maintenance.STEPS` to avoid wasting the phone's cycles repeating
identical historical windows. Old private experiment reports remain available,
and **quarterly, sector, annual and readiness monitoring remains enabled**.
Manual execution of old research scripts is still possible. Fresh sessions
cannot be considered independently validated by retroactively changing
parameters on the inspected window.

Read-only diagnostic now captures each simulated trade's initial/exit stop,
opening gap below stop, whether a daily OHLC bar touched both stop and
target, trailing stop status and partial profit-taking. The stop-first
assumption and P&L calculations are unchanged. `--brief` prints a compact
loss-attribution report, and `--strategies` narrows diagnostic work without
selecting a purported winner. Example:

```bash
cd ~/nse-fundamentals-monitor- && git pull
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_loss_diagnostic.py --limit 100 --top 3 --brief --strategies vcp-breakout-v1 mean-reversion-v1 custom-dip-reclaim-v1
```

All data are read offline, output is private under
`runtime/experiments/loss-diagnostic-v1/report.json`. Do not interpret a
large >35% single-day jump as an automatic data error; investigate exchange
source and adjustments first. No strategy is promoted on this report.

## Research-only strategy comparison and ranking (16 October 2026)

- The daily Termux research maintenance now compares the same deterministic
  100-stock sample of **breakout-v1** and **trend-pullback-research-v1**, if
  private validated adjusted OHLCV is present. No GitHub runner has that
  private CSV and GitHub unit tests do **not** establish real-market returns.
- Completed-trade net results now exclude forced end-of-sample liquidations;
  original figures including those forced exits remain separately labeled.
  Normal and doubled costs/slippage and source/implementation hashes are kept.
- New `research_monitor/exploratory_ranking.py` describes a research leader
  only if **at least 30 pooled completed trades in each cost run**, **at least
  10 symbols with completed trades**, positive completed net means including
  double-cost stress, profit factor >=1.5, and worst per-stock drawdown <=20%.
  Its ranking metric is completed net mean under doubled costs. Even if these
  screening gates pass, any leader is marked **EXPLORATORY** and **NOT
  VALIDATED**. Pooled overlapping trades are not portfolio returns.
- `project_status.py` reports the exploratory leader only when both current
  input metadata hash and implementation SHA match the local experiment report.
  Stale results are blocked. Verified profitable strategies remain **0**
  and **no automatic trading, candidate promotion, SMS or order execution**
  is introduced.
- GitHub Actions unit/regression/cloud smoke checks pass for this logic using
  synthetic examples. A real 100-stock run on the private Termux OHLCV, real
  profitability result, independent fresh evaluation, point-in-time fundamental
  screening, and forward paper observations have **not** been independently
  confirmed. The user's earlier 20-stock local result (11 completed pullback
  trades) is insufficient for proof.

## Separate trend-pullback experiment — not enabled for trading

Added `swing_pullback_research.py` with frozen, explicit research-v1 rules:
close > EMA50 > EMA200, reclaim of EMA20 from the prior close at/below EMA20,
RSI 50–72, volume at least its 20-bar average, average turnover >= INR 5 crore,
and the existing ATR/stop-distance limits. Entry/exit/cost model is reused.
This is a hypothesis-driven experimental strategy, not a proven improvement.
No active strategy, candidate list, alert gate or paper plan is switched.

It compares the original and experimental strategy on the same deterministic
20-symbol sample by default (maximum 100 per invocation). Both base and doubled
cost/slippage runs are exploratory. Sampling is based on symbol hash, not returns.
Only history through 15 October 2026 is used; post-design observations are excluded.
The old holdout is already consumed for design and is never called independent
validation for the new version. Source hashes and the complete protocol are
recorded under `runtime/experiments/trend-pullback-research-v1/comparison.json`.
No future win probability, portfolio result or live pass is generated.

```sh
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_pullback_research.py --limit 20
```

This reads existing history and runs a bounded comparison, not history collection
or a full-universe backtest. More signals alone are not an investment improvement.
Point-in-time universe/fundamentals, portfolio risk, fresh evaluation data and
forward observations remain outstanding. Fundamental/sector blockers are unchanged.

## Filter-level signal investigation

Owner supplied signal audit: 660 history-eligible stocks, 273,570 holdout-day
observations, 357 signals and at most 238 eligible next-open entries ignoring
occupied positions. 475 stocks have no signals; 518 have no eligible entries;
142 have only 1–9. No stock can meet the fixed 30-completed-trade threshold on
this dataset with the current rules. This does not prove future unprofitability.

Added per-filter independent pass counts and a sequential rejection breakdown to
`swing_signal_audit.py`, plus missing indicator and zero-volume counts. Signal
conditions are shared with the backtest, not an alternative strategy. Synthetic
regression checks confirm exact equivalence with the old boolean expression,
past indicators unchanged by future bars, basic RSI edge cases, and ATR gaps.
169 local tests passed. No calculation error has been demonstrated by these tests;
actual phone filter counts are still needed. Criteria and trading behavior stay
unchanged. Compact read-only command:

```sh
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_signal_audit.py --filters-only
```

Since the existing holdout is now being inspected for strategy design, it cannot
serve as untouched validation for any future revised strategy. A new strategy
requires independent evaluation and forward observations, not threshold tuning
until the same historical stocks pass.

## Signal-entry audit and industry-source investigation

Owner diagnostics confirm all 660 history-eligible symbols have fewer than 30
completed holdout trades; 423 more lack 1,260 bars. Nonpositive mean returns can
include no-trade samples and must not be described as 614 losing strategies.
Added `swing_signal_audit.py`: reads validated local OHLCV, counts prior-bar
signals and next-open entry rejections in the unchanged 30% holdout. Counts every
potential entry irrespective of existing positions, so eligible counts are upper
bounds, never completed trades. It makes no network calls or writes and does not
run the five backtest simulations. Phone execution is still needed for real counts.

```sh
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_signal_audit.py
```

The exact entry rule uses the next open plus 0.1% illustrative slippage, accepts
only prices between signal close and 1% above it, and rejects stop distance above
12%. No change to these rules or pass thresholds has been made.

Official alternative source tested on 16 October 2026:
https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv
returned HTTP 200 and 755 rows with Company Name, Industry, Symbol, Series and
ISIN Code. This offers partial classification for an index subset, not the full
four-level classification of all target companies. It has NOT been substituted
for the strict sector gate; broad labels could miss specialist-sector exceptions.
Full local regression suite including signal-audit tests: 164 tests passed.

## Termux evidence supplied by owner — 16 October 2026, around 14:06 IST

The phone's pasted status reports adjusted NSE history published for 1,083 symbols
(1,365,517 rows), covering 2020-01-01 through 2026-10-15. 660 symbols have at least
1,260 bars; no unresolved archive-session gaps were reported. 222 symbols remain
excluded for corporate-action review. These are owner-supplied runtime results,
not a fresh remote inspection of the phone or independent source audit.

All eight post-publication scripts exited successfully. This means the pipeline
completed, not that its investment criteria passed: strict annual/shortlist and
technical passes are zero. The provisional two-year screen has 478 passes but
zero current provisional candidates. Official sector rows are zero; quarterly
collection reports BOUNDED_ATTEMPT_LIMIT_REACHED. Upstox is deferred, SMS setup
pending and entry alerts blocked. Supervisor/dashboard were running with wake lock.

Termux now has separate history/research workers, loopback MFA dashboard,
crash restart, optional Termux:Boot and encrypted backup/restore commands.
Actual phone force-stop/reboot recovery and restore should not be inferred from
code tests. Render is an optional alternate host, not a prerequisite for the
phone's current research pipeline.

## Diagnostic improvement

Backtest reports previously collapsed every eligible-history rejection into
INSUFFICIENT_OR_WEAK_EVIDENCE. Future runs record every failed fixed threshold
and development/half-period metrics. Thresholds are unchanged. The new read-only
`diagnose_research.py` reports existing backtest, annual, sector and quarterly
blockers without contacting a broker or rerunning history. Older reports expose
only partial backtest evidence; the diagnostic explicitly marks missing detail.

Phone command (does not stop the backend):

```sh
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python diagnose_research.py
```

The phone's detailed reports are still needed to identify its actual rejection
counts. No source gap or zero-pass result has been declared fixed by this change.

## Latest update: cloud deployment package (not yet hosted)

- Added a single-instance Render Blueprint, supervised Gunicorn + worker startup,
  shared persistent data paths across research modules, and a minimal health probe.
- Failure of either child stops its sibling and exits for a platform restart.
  Shutdown also terminates worker subprocesses. Source snapshots refresh at deploy;
  private history, auth state and paper databases survive. No SMS is enabled.
- Offline checks now run even when the exchange calendar/Upstox is unavailable.
  The worker never infers that a market session is open from missing calendar data.
- Added private server recovery-code rotation and an operator deployment runbook.
- Verification: **108 unit/regression tests passed**. A real local Gunicorn + worker
  smoke run checked startup, HTTP guards, login, session persistence and TOTP replay
  rejection after restart, offline readiness, and sibling shutdown on worker failure.
  These checks use temporary synthetic credentials; Render itself is not verified.
- Still external: Render account/service creation and private enrollment, ingress
  and off-service backup/restore acceptance, licensed adjusted history, missing
  verified annual facts, forward observation time, risk/capital inputs, and SMS
  provider/event orchestration. Upstox activation/live testing remains deferred.

## Latest source-run result (16 October 2026)

The earlier cloud recovery run completed: 100 legacy XML candidates examined,
0 accepted annual rows; stored quarterly rows increased to 1,774 and final
shortlist stayed 0. These quarterly records were produced by the earlier
parser, so the new v6 re-verification gate must still be satisfied before they
can qualify. This result is separate from paper-engine unit tests.

## Latest update: persistent forward paper research

- Added a SQLite paper ledger with frozen plan registration timestamps, duplicate suppression and completed-session replay using the existing strategy engine.
- Registration after the next session opens is rejected. Missing calendar days/bars wait; revised historical inputs require review. Open positions remain open at the available-data boundary.
- Gap stop losses, partial targets, trailing stops and time exits follow the shared model. Results are normalized per-stock EOD observations, not portfolio simulation, real fills or live exit alerts.
- Worker archives verified calendars and runs paper/readiness checks before market and after close. Existing observations can advance even if a new screen fails. Dashboard shows timestamped paper status.
- Full local suite: **100 tests passed**. Actual run without history: `WAITING_FOR_VALID_ADJUSTED_HISTORY`, zero plans, zero real orders, forward-validation flag false.
- Still blocked by real adjusted history, verified calendar coverage, sufficient forward observation time, user capital/risk configuration, SMS provider integration and cloud deployment. No successful real-market paper performance is claimed.

## Previous update: quarterly date correctness and re-verification

- Fixed a reproduced ISO-date bug: `2025-04-01` was read as January 4. ISO dates now keep their year-month-day meaning; invalid ISO dates cannot fall back to a swapped date.
- Quarterly parser version is now 6. Stored earlier records remain available for audit but cannot contribute comparisons or final shortlist eligibility until reverified. Successful download-ledger entries alone cannot suppress a missing/current-version raw result.
- Recovery prioritizes existing rows requiring re-verification; source status reports separate stored rows, current-parser verified rows and rows still requiring re-verification.
- QoQ/YoY uses the exact previous quarter/year end, including unequal month lengths. Missing intermediate quarters no longer prevent an otherwise valid same-quarter year comparison. Missing/zero/nonfinite baselines do not generate growth percentages.
- All 90 local regression tests passed, including all four quarterly XML periods, parser migration, month-end boundaries and stale-source blocking.
- Workflow updates queue behind an active fundamentals run instead of canceling it. More verified quarterly coverage is expected only after real source collection finishes; no increased coverage or new shortlist is claimed from tests.

## Previous update: legacy annual source recovery

- Added a bounded official-XML verifier for FY2022–23/FY2023–24 and attached source/index hashes to accepted annual rows.
- Verifies company identity, exact financial-year context, audit, consolidated basis, monetary units and duplicate facts; never turns metadata alone into verified financials.
- Runs before integrated annual fetching in the daily workflow. 403/429, server and network failures stop requests; rejected filings do not overwrite history.
- Actual source check: 11 distinct official filings downloaded across two bounded local runs. All 11 lacked the exact April–March context required by the verifier; **0 new annual rows** were accepted. This is a document-context issue, not proof that annual statements do not exist. Corrected XML or independently verified annual statements are still needed.
- Full local regression suite now has 80 passing tests. Current final shortlist remains 0; no trading-readiness claim is made.

## Earlier completed offline-history update

- Offline adjusted-history CSV import, with exact-byte SHA256 matching and supplied source/adjustment attestation checks. No broker account is needed. This validates the input structure, not provider truth.
- Shared validation for imports and actual backtests: missing symbols, invalid OHLCV, duplicate daily bars, malformed/intraday dates and future bars are rejected. Current-day bars remain excluded.
- Importing new history invalidates old reports/plans. Failed screening removes old candidates and their manifest.
- Dashboard hides candidate files without a fresh manifest or when any linked historical, fundamental or backtest input changes. Plans expire from display after 24 hours; all remain conditional research.
- `python swing_readiness.py` reports local missing inputs, history coverage, matching-data technical pass count, worker freshness and unresolved operational dependencies without contacting a broker. Dashboard shows the last check timestamp.
- GitHub test workflow runs the complete unittest suite.

## Verification

Local full suite: 68 tests passed, including synthetic end-to-end history → backtest → zero eligible candidates. Existing authentication, risk, SMS and fundamental source tests also pass. Synthetic fixtures establish software behavior only; no investment performance or real SMS delivery was tested.

## Earlier evidence snapshot and remaining work

The following coverage numbers are the 15 October snapshot; use the newer run result above for 16 October:

| Area | Evidence | Remaining work |
|---|---|---|
| FY2024–25 | 1,349 annual data-quality passes | Complete full multi-year/sector criteria |
| FY2025–26 | 1,528 annual data-quality passes | Complete full multi-year/sector criteria |
| FY2022–23 | 1,277 unverified index candidates; 0 verified historical XBRL rows | Verify original audited consolidated annual facts; 470 symbols still lack a candidate/review |
| FY2023–24 | 1,400 unverified index candidates; 0 verified historical XBRL rows | Verify original audited consolidated annual facts; 347 symbols still lack a candidate/review |
| Quarterly | 1,750 verified records; 0 qualified comparisons | Obtain eligible full annual screens and qualified comparisons |
| Final shortlist | 0 of 1,747 screened | Resolve data/source gaps without weakening filters |
| Adjusted OHLCV | Import and validation implemented; real dataset unavailable in this checkout | Obtain authorized adjusted price AND volume history and validate source evidence |
| Backtesting | Fixed-rule engine and regression tests | Run on real history; point-in-time fundamentals/universe, portfolio and forward paper validation remain incomplete |
| Upstox | Read-only polling code | User account activation pending; token and real feed verification deferred |
| SMS | Guarded transport code | Provider/template setup, event orchestration and delivery-receipt reconciliation |
| Cloud | Deployment package and local process smoke test implemented | Create actual hosting, enroll private credentials, verify ingress and backup/restore |

No orders are placed. No future success probability is calibrated. Having a token or passing unit tests does not make the system live-ready. Local Windows files are not automatically updated by a GitHub commit.

Progress in this file is a dated snapshot, not a background service or recurring notification.
