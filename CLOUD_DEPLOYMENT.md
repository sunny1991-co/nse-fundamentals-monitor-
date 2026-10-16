# Render deployment — private, single-owner research

Deployment package implemented; no Render service has been created or verified.
Upstox is optional for startup and remains deferred. No orders or SMS are sent.

## Architecture

One paid native Python web service supervises Gunicorn and the research worker.
Both use `/var/data/nse` on the same persistent disk. Do not split them into two
Render services: disks cannot be shared across services. Code stays in the release
checkout; all research modules use `NSE_DATA_DIR` for mutable data. Local operation
without that variable keeps the existing repository-directory behavior.

`render.yaml` specifies Singapore, one 1c-2g instance and a 5 GB disk. These are
initial capacity settings, not a measured production sizing or price guarantee.
Review Render's displayed cost before creating the service. Disk services have
restart/redeploy downtime. Autoscaling and multiple workers are unsupported.

## Private setup

1. Sign into Render with GitHub. New → Blueprint → select
   `sunny1991-co/nse-fundamentals-monitor-`, branch `main`.
2. On your own trusted computer, in a private terminal with dependencies installed,
   run `python setup_auth.py --host pending.invalid --db /ABSOLUTE/PRIVATE/auth.sqlite`.
   Substitute a real absolute local path. Complete password and Authenticator
   enrollment. Keep the printed recovery codes private.
3. From the ignored local `.env`, put ONLY `APP_PASSWORD_HASH` and
   `APP_TOTP_SECRET` into the Blueprint's requested secret fields. Render generates
   `APP_SECRET_KEY`. Do not paste credentials into chat or commit `.env`.
   The launcher derives the actual host from `RENDER_EXTERNAL_HOSTNAME`, and forces
   the auth database to `/var/data/nse/runtime/auth.sqlite`. Do not copy the local
   APP_HOST or APP_AUTH_DB values into Render.
4. Review the paid resources and create the Blueprint. Leave Upstox unset.
   The app creates its server auth database using the supplied password hash and
   TOTP seed. The local recovery codes are NOT present in that fresh server DB.
5. In Render's private Shell, run `python cloud_recovery_codes.py` once to create
   server recovery codes. It requires interactive confirmation and replaces any
   previous codes. Store these new codes offline; discard the unused local codes.
6. Open the Render HTTPS URL and verify password + Authenticator login. HTTP
   requests must redirect at Render's edge; a direct insecure application request
   must fail. Check logout and a failed-login attempt. Do not enable a custom
   domain until `APP_HOST` matches it and the health-check Host is verified.

Render terminates HTTPS. The Render-specific Gunicorn config trusts only
`X-Forwarded-Proto` for the scheme, not forwarded host or client identity. It must
never run on a directly internet-accessible origin. `TRUST_SINGLE_PROXY` is
forced off to avoid applying forwarding logic twice. Verify HTTP redirects,
HTTPS login and the actual routing before considering deployment accepted.

## Acceptance and operation

- `/healthz` gives only `ok`/`unavailable`, without cookies or private details.
  It checks the auth DB can be read and the process supervisor heartbeat is fresh.
  It is process health, **not** proof of market-data freshness or trade readiness.
- Dashboard should show `CALENDAR_PENDING`, disconnected feed and blocked alerts
  while Upstox is pending. Offline research/readiness checks still run on startup
  and daily; no market sessions are guessed.
- Kill a child only in a test deployment: the supervisor must stop its sibling,
  exit nonzero and allow Render to restart the whole service.
- Restart and redeploy; verify the paper ledger, Authenticator replay/lockout
  state, history imports and recovery-code state persist.
- Public fundamental snapshots listed in `cloud_start.SOURCE_FILES` refresh from
  the deployed GitHub revision before children start. Derived shortlist/plans
  are invalidated and recomputed. Private OHLCV, metadata, watchlist, calendar and
  all SQLite databases are preserved.
- Automatic deploys are off. GitHub Actions collecting new filings does **not**
  update an already-running Render instance. Deploy the latest verified commit
  explicitly to refresh bundled public snapshots; check report dates afterward.
- Render's service notifications report deployment failures. App-level status is
  on the dashboard; no automatic SMS reporting is configured.
- Before importing history or running maintenance, export `NSE_DATA_DIR=/var/data/nse`
  in the private Shell. Do not start a second worker. Private data is never a Git
  artifact. The service account must be the only account with disk access.

For a consistent backup, stop both processes and copy the complete private data
folder through an operator-controlled encrypted backup channel; preserve its
file permissions. Restore with processes stopped, then test login and paper
state before opening access. Never commit or attach the auth DB, `.env`, or
licensed price history to an issue. A verified off-service backup and restore
exercise still requires the hosting operator; this package does not configure it.

## Remaining external dependencies

Actual hosting/account access, private enrollment, restart/restore and ingress
acceptance tests require the operator's Render account. Full research validation
also needs verified historical annual facts, licensed adjusted OHLCV, calendar
coverage and elapsed forward observation time. SMS provider/template approval,
capital/risk inputs and end-to-end alert orchestration remain separate work;
they are not completed by activating Upstox. No live-ready claim is made.

Reference: https://render.com/docs/blueprint-spec,
https://render.com/docs/disks, https://render.com/docs/health-checks,
https://render.com/docs/deploy-flask (reviewed 16 October 2026).
