# Swing equity setup

Active product: equity swing research only. F&O and IPO are disabled in the UI/CLI and risk/notification entry points. No real orders are sent. Existing password + authenticator login remains required.

## Current readiness

Implemented: strict fundamental filters, chronological technical holdout backtest, conditional entry/stop/target plans, equity-only risk sizing, authenticated dashboard, session supervisor, Upstox read-only quote polling, deduplicated SMS transport.

NOT complete/live: verified historical adjusted data ingestion, point-in-time fundamental backtest integration, forward paper performance validation and portfolio-level paper execution, automatic event-to-SMS orchestration, SMS delivery-receipt reconciliation. No calibrated future success probability exists. A passing technical backtest alone does not validate the full fundamental+technical strategy.

A zero shortlist means no trade: missing evidence must not be replaced with guessed sectors or relaxed gates. Alerts remain blocked pending forward validation even when quotes connect.

## Windows

Preserve your existing private `.env` and auth database when updating project files. Inside the activated existing virtual environment run `python -m pip install -r requirements.txt`. Keep the existing dashboard launcher. Double-click `Start_Swing_Worker.bat` inside this project folder for the separate worker. Closing its window stops monitoring. Laptop must be on, connected and awake. This does not install a Windows startup task or wake a powered-off laptop.

Worker polls every 10 seconds while the verified session is open; this is periodic quote monitoring, not tick-by-tick streaming. It checks exchange status separately. Holidays/special hours come from Upstox timings, not a hardcoded Monday-Friday assumption. No token/calendar: CALENDAR_PENDING, no live signal. On a valid non-trading day or after close it runs existing annual/shortlist/technical analysis once; restarting cannot duplicate that job. EOD data must be independently refreshed before research can succeed. Failures are visible in `runtime/analysis.log`; no repeated CPU-heavy retry loop. Current historical loader excludes the current calendar day, so after-close results use prior completed dates until that ingestion policy is updated.

Set `UPSTOX_ANALYTICS_TOKEN` privately in `.env` after account activation. Never paste it in chats or GitHub. `swing_watchlist.json` is an array of independently verified `NSE_EQ|ISIN` instrument keys, max 500; do not infer keys from stock names. Quotes with missing instruments, stale timestamps, invalid prices or wide spreads are not eligible.

## SMS

Transport uses MSG91 Flow API with an approved provider/DLT-mapped template. Provider setup/charges are separate; no payment or account creation performed by this code. Private environment keys:

- SMS_ENABLED=1 (leave unset until configured)
- SMS_TEMPLATE_APPROVED=1 (only after approval)
- MSG91_AUTH_KEY
- SMS_TEMPLATE_ID
- SMS_RECIPIENT (91 plus your ten-digit mobile number)

Approved template placeholders must be `symbol`, `event`, `entry`, `stop`, `targets`, `expiry`, `scope`. Example template for provider review (not an approval):

`Swing {symbol}: {event}. Entry {entry}, SL {stop}, targets {targets}. Valid until {expiry}. Scope {scope}. Research only; no order sent.`

Full technical/backtest/risk evidence remains in the application/outbox. Provider acceptance is not proof of delivery. Timeout produces DELIVERY_UNKNOWN_DO_NOT_RETRY; reconcile in provider dashboard before any resend. SMS can arrive late; expired entry alerts are not usable. No actual message has been sent by the tests.

Official references: https://upstox.com/developer/api-documentation/analytics-token/ ; https://upstox.com/developer/api-documentation/get-market-timings/ ; https://upstox.com/developer/api-documentation/get-full-market-quote/ ; https://docs.msg91.com/sms/send-sms


## Offline preparation while Upstox activation is pending

No account or token is needed for these commands:

```
python swing_readiness.py
python swing_import_history.py --csv "path/to/adjusted_daily.csv" --metadata "path/to/provider_metadata.json"
python swing_technical_screener.py
python swing_readiness.py
```

Supply `Symbol,Date,Open,High,Low,Close,Volume`; dates must be exactly `YYYY-MM-DD`.
The metadata JSON must contain `source`, `adjustment_method`,
`corporate_actions_adjusted: true`, and `sha256` of the exact original CSV bytes.
Only attest adjustments after checking provider evidence for prices AND volume.
The importer does not download data, adjust prices itself, verify an exchange
instrument mapping, or establish that a provider attestation is true.
No demo prices are installed as production data. At least 1260 completed bars
per stock remain necessary, and meeting that minimum does not imply a pass.

Import validates everything before replacing existing data. It rejects missing
symbols, malformed daily dates, future dates, duplicates and invalid OHLCV.
Current-day rows remain excluded. Successful imports invalidate previous
backtest and candidate outputs. Raw licensed history and generated plans are
ignored by Git and must stay private.

The dashboard displays only conditional plans with a fresh (at most 24 hours)
manifest whose hashes still match the historical data, metadata, fundamental
reports, backtest report and candidate file. Older pre-manifest plans must be
regenerated. A failed screener removes previous plans. The local readiness
report includes its check timestamp; it is not continuous cloud monitoring.
Entry SMS stays blocked pending forward validation.

## Forward paper research ledger

`python swing_paper_monitor.py` registers fresh evidence-gated candidates and
reconciles prior plans against completed daily bars. The worker invokes it
before the market and after close, including when a fresh fundamental screen
fails, so prior observations are not lost. It archives each verified daily
calendar under `runtime/calendar_history.json`; missing days cannot be silently
assumed to be holidays. Keep that file and `runtime/paper.sqlite` on persistent
private storage.

This is normalized one-unit **per-stock EOD research**, not a portfolio or live
broker execution simulator. It reuses the backtest's fills, costs, stop-first
assumption, gap stops, partial targets, trailing stops and time exits. An open
position is not liquidated merely because today's available data ends.

Registration must precede the next verified session opening. Late registration
is permanently skipped rather than backfilled. Restarts cannot duplicate a
plan or trade. Missing sessions/bars wait; revised pre-signal or already observed
prices require review. No automatic split/bonus reconciliation is implemented.
A fresh dataset should be imported and screened before the next session opens.
Current-day bars are excluded, so observations lag by at least one completed
session; these reports are not intraday exit alerts. Starting after the opening
can miss that day's entry, deliberately.

Dashboard and `runtime/paper_status.json` expose the latest check and states.
The engine never sets `forward_validation_pass` true, sends SMS or places an
order. Real history, calendar coverage, enough forward observations, portfolio
risk configuration and independent review remain necessary. No user capital
has been invented. Without adjusted history the status is
`WAITING_FOR_VALID_ADJUSTED_HISTORY`.
