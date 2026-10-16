# Project progress — 16 October 2026

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
