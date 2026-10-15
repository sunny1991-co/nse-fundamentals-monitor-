# Private research app — implementation status

This is a research foundation, not a completed live trading product. No broker order or IPO application endpoints exist. The dashboard is an authenticated status shell, not a finished trading UI.

## Implemented

- Password (scrypt) plus Authenticator TOTP required together on every login.
- Persistent account-wide lockout after five failures (15 minutes); TOTP replay prevention across workers; hashed single-use recovery codes, still requiring the password.
- Hashed server-side session records, 15-minute idle and 8-hour absolute expiry, logout invalidation, CSRF tokens, Secure/HttpOnly/SameSite cookies, HTTPS-only requests and restrictive headers.
- No default production credentials. Missing secrets refuse startup. Broker tokens stay in server environment; never put them in chat, browser JS, logs or GitHub.
- Read-only Angel One FULL quote and candle adapter. Missing quotes, bad timestamps and stale/wide-spread data block eligibility.
- Risk sizing rejects missing capital/account data, exhausted risk budget and daily-loss limits. Long-option size considers loss of entire premium. Futures/short options are not implemented.
- IPO review evaluator: mainboard, official RHP source, verified financials and disclosure review. This requires independently reviewed input and does not automatically extract/verify a prospectus. No GMP-based approval and no invented pre-listing backtest.
- Shared equity technical backtest and entry/exit logic from SWING_VALIDATION.md.

## Setup on trusted server

Install `pip install -r requirements.txt`. Enroll interactively using `python setup_auth.py --host YOUR_HOST --db /PERSISTENT_PRIVATE_PATH/auth.sqlite`. The script asks for your new password, shows your Authenticator seed once and confirms a working code. Save recovery codes offline. Do not execute this enrollment in a shared assistant transcript. Import the ignored .env values into the hosting provider's encrypted secret settings; the app does not auto-load .env.

Run `gunicorn --workers 1 --bind 0.0.0.0:$PORT 'app:create_app()'` behind HTTPS. APP_HOST must be the exact hostname. Enable TRUST_SINGLE_PROXY=1 only after verifying that the deployment has exactly one trusted proxy that overwrites X-Forwarded-Proto and that clients cannot bypass it. Otherwise leave it off. Use persistent private storage for APP_AUTH_DB; an ephemeral disk is not suitable for MFA replay and lockout state. Restrict file access and maintain protected backups. After any credential reset rotate APP_SECRET_KEY and revoke all sessions. Recovery-code rotation is a server-admin operation; no public reset endpoint exists.

This configuration has NOT been deployed or penetration tested. Verify proxy trust, HTTPS, persistent storage, Authenticator enrollment and backup recovery end-to-end before enabling user access. Prefer managed identity/passkeys for a production multi-user service; this implementation is single-owner only.

## Data setup

SmartAPI environment: ANGEL_API_KEY, ANGEL_ACCESS_TOKEN, ANGEL_LOCAL_IP, ANGEL_PUBLIC_IP, ANGEL_MAC_ADDRESS. Access token renewal is manual in this version. Do not pass PIN, OTP or API keys through chat. Call `python monitor.py quotes --input watchlist.json` with at most 50 verified NSE/NFO tokens. This makes one request; it is NOT a running WebSocket/live alert service. Token IDs must be resolved from the current official instrument master, not guessed. Historical candle adjustment verification remains required by the backtest.

Copy monitor_config.example.json to ignored monitor_config.json and set actual capital only after the user supplies it. Position sizing has conservative draft limits, not a suitability assessment. Account free cash, open risk, marked daily P&L and exchange calendar/session status must be current and verified.

## Still required for the requested full app

1. Complete annual/quarterly/sector data verification; currently no full fundamental passes.
2. Licensed adjusted historical equity data, actual expired option contract data, independent holdout review, benchmark and forward paper tests.
3. Continuous quote ingestion, token renewal, exchange calendar, alert delivery, durable portfolio/paper-trade ledger and reconciliation. No F&O execution simulator is wired to a live feed yet.
4. Automated official IPO discovery and reviewed RHP extraction; known blocked sources must remain visible as errors.
5. Finished responsive UI, Render hosting configuration and security enrollment, browser/end-to-end testing, backups/restore tests and monitoring.
6. User capital and accepted loss budget. No automatic real-money derivatives orders. Trading remains manually confirmed in the broker.

The app must never claim 'nothing missing', guaranteed return, or a calibrated high probability until the missing evidence exists. A successful workflow only means the program ran, not that stocks qualified.

## Derivatives coverage requested
Index (including NIFTY 50) and exchange-listed stock options/futures are in the research scope. contracts.py validates OPTIDX/FUTIDX/OPTSTK/FUTSTK records. Official instrument IDs, current lot sizes, expiries, tick sizes and option strikes must be verified each day. Stock F&O additionally requires settlement and ban-list review; futures require margin/stress review. This registry does not imply completed contract-data integration, strategy backtests or execution. Current risk sizing implements equity and long-option paper plans only; futures and short-option sizing remain blocked.

## Additional quality gates

New research context checks require aligned 200-session stock/index histories, index trend above its 200-session average with its 50-session average also higher, and stock outperformance over 63 sessions. A fresh verified event calendar blocks new plans within 72 hours of a known material event. Draft exposure limits: 10% per stock and 25% per sector. These are added research gates, not proven improvements. They MUST be integrated into historical point-in-time and forward testing before claiming the combined strategy passed. Equity risk plans now require explicit evidence for market regime, relative strength, event clearance and portfolio concentration; no evidence means no plan. Benchmark validation, corporate actions, earnings events and fees/slippage remain essential. No additional indicators or thresholds should be tuned against the holdout to manufacture a high win rate.

## Confirmed notification flow

Paper trading is intended to run automatically; the user does not approve paper entries. Only evidence-qualified new setups and relevant exit/trailing events should reach WhatsApp. The user places actual orders manually in Groww. Personal holding alerts require a user-recorded, reconciled position; paper exits must stay labeled PAPER_RESEARCH. alerts.py prepares a persistent outbox with origin-event deduplication, fresh quote timestamps, expiry and explicit backtest/forward-paper/risk gates. It does NOT send messages. The channel must recheck expiry immediately before actual sending. The WhatsApp connection, verified recipient, templates, retry/delivery tracking and continuous paper engine are pending. No WhatsApp recipient or credentials are stored in this repository.

Swing and F&O must use separate watchlists, paper ledgers, capital/risk budgets, performance reports and notification labels. monitor_accounts declares separate storage paths; the continuous ledger runners still need implementation. Alert origin IDs include SWING/FNO to prevent cross-module event collisions. Never present a combined win rate or apply equity validation to derivatives.
