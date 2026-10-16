# Private equity swing research app

Active scope is NSE equity swing only. F&O and IPO are disabled in the app.
Read-only Upstox is the planned primary feed; account activation is pending.
Actual trading remains manual in the user's Groww app. No broker order routes
or automatic real-money execution are implemented.

## Current implementation

- Password plus TOTP, session expiry, lockout/replay protection, recovery codes,
  CSRF, HTTPS-only cookies and private dashboard.
- Strict fundamental/quarterly gates, source recovery, history import,
  chronological technical backtests and conditional research plans.
- Read-only quote polling and verified market-session supervisor.
- Persistent per-stock EOD paper observations, independent of real holdings;
  no portfolio allocation or calibrated future win probability.
- Guarded SMS transport exists; provider setup, event-to-message orchestration
  and delivery-receipt reconciliation remain incomplete.

## Setup on trusted server

Install `pip install -r requirements.txt`. Enroll interactively using `python setup_auth.py --host YOUR_HOST --db /PERSISTENT_PRIVATE_PATH/auth.sqlite`. The script asks for your new password, shows your Authenticator seed once and confirms a working code. Save recovery codes offline. Do not execute this enrollment in a shared assistant transcript. Import the ignored .env values into the hosting provider's encrypted secret settings; the app does not auto-load .env.

Run `gunicorn --workers 1 --bind 0.0.0.0:$PORT 'app:create_app()'` behind HTTPS. APP_HOST must be the exact hostname. Enable TRUST_SINGLE_PROXY=1 only after verifying that the deployment has exactly one trusted proxy that overwrites X-Forwarded-Proto and that clients cannot bypass it. Otherwise leave it off. Use persistent private storage for APP_AUTH_DB; an ephemeral disk is not suitable for MFA replay and lockout state. Restrict file access and maintain protected backups. After any credential reset rotate APP_SECRET_KEY and revoke all sessions. Recovery-code rotation is a server-admin operation; no public reset endpoint exists.

This configuration has NOT been deployed or penetration tested. Verify proxy trust, HTTPS, persistent storage, Authenticator enrollment and backup recovery end-to-end before enabling user access. Prefer managed identity/passkeys for a production multi-user service; this implementation is single-owner only.

## Active setup and progress

Follow [SWING_SETUP.md](SWING_SETUP.md) for worker, Upstox, offline history,
SMS configuration and paper-ledger behavior. Review
[PROJECT_PROGRESS.md](PROJECT_PROGRESS.md) for the latest tested milestones.

`python swing_readiness.py` checks local evidence without a broker connection.
`python swing_paper_monitor.py` advances forward EOD observations when valid
history and verified calendars exist. Neither command sends messages or orders.
Keep `runtime/`, the authentication database and licensed history private and
persistent. A GitHub commit does not update the user's Windows checkout.

Cloud deployment, actual historical-data backtests, enough forward paper
observations, portfolio risk integration, SMS setup and end-to-end validation
remain pending. A green test run is not a verified stock shortlist or investment
performance result. Entry alerts remain blocked until their evidence gates pass.

## Prepared cloud package

See [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md) and `render.yaml` for the single-service persistent Render setup. It has not been deployed. Use `python cloud_start.py` on Render, not a dashboard-only Gunicorn command.
