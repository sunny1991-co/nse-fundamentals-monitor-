# Android / Termux backend

This mode keeps the existing equity swing research worker running on the user's
own Android phone. Upstox access remains read-only. No broker order is placed.

## What is implemented

- `termux_supervisor.py` keeps one `swing_worker.py` process alive and restarts
  it after a crash.
- It uses `termux-wake-lock` when available.
- `termux_backend.sh` provides start, stop, restart, status and one-shot commands.
- An optional Termux:Boot script can be installed for phone reboot recovery.
- Mutable research data and runtime state stay under `~/nse-fundamentals-data` by default, outside the Git worktree.
- Private credentials remain in ignored `.env`; never commit or paste them into
  chat.
- Android can still stop Termux after force-stop, battery optimization, vendor
  background restrictions, reboot without Termux:Boot, or loss of power/network.

## First-time setup

Run these commands in Termux from the repository directory:

```sh
git pull
chmod +x termux_backend.sh
python -m pip install -r requirements.txt
./termux_backend.sh once
./termux_backend.sh start
./termux_backend.sh status
```

If dependency installation fails, stop and capture the exact error rather than
using random package workarounds. The project has previously encountered Python
package build issues on Android, so resolve the actual failing package first.


## One-time migration from repo-local data

Older Termux installs wrote generated CSV reports into the Git checkout. Before
pulling a version that uses the private data directory, preserve those local
results and clean the worktree:

```sh
./termux_backend.sh stop
mkdir -p "$HOME/nse-fundamentals-data/runtime"
cp -n ./*.csv "$HOME/nse-fundamentals-data/" 2>/dev/null || true
cp -a runtime/. "$HOME/nse-fundamentals-data/runtime/" 2>/dev/null || true
rm -f "$HOME/nse-fundamentals-data/runtime/termux_supervisor.pid" \
      "$HOME/nse-fundamentals-data/runtime/termux_supervisor.lock"
git restore --worktree --staged -- '*.csv'
git pull
chmod +x termux_backend.sh
./termux_backend.sh init-data
./termux_backend.sh install-boot
```

The bootstrap copies tracked public CSV snapshots only when the private copy is
missing. It never overwrites newer phone-local data.

## Private mobile dashboard enrollment

The backend can expose the dashboard only on the phone itself at
`http://localhost:8787`. It is not bound to Wi-Fi/mobile-network interfaces.
Password + Authenticator remain mandatory even in this local-only mode.

Stop the backend and enroll privately in Termux (never paste the password,
Authenticator seed, current code, or recovery codes into chat):

```sh
./termux_backend.sh stop
mkdir -p runtime
python setup_auth.py --host localhost --db "$HOME/nse-fundamentals-data/runtime/auth.sqlite"
./termux_backend.sh start
./termux_backend.sh status
```

After successful enrollment, status should show `dashboard: LOOPBACK_MFA_ENABLED`
and a dashboard PID. Open `http://localhost:8787` in the same phone's browser.
The local HTTP exception is accepted only for loopback clients; non-loopback HTTP
requests are rejected and the server never binds to `0.0.0.0`.

## Private Upstox setting

After the Upstox analytics/read-only token is available, edit the ignored
`.env` locally on the phone. Do not send the token in chat or commit it.

```sh
nano .env
```

Add or update:

```text
UPSTOX_ANALYTICS_TOKEN=YOUR_PRIVATE_TOKEN
```

The token alone does not establish validated live monitoring. A verified
`swing_watchlist.json`, exchange calendar, successful real quote checks and
fresh worker status are also required.

## Keep Android from suspending it

In Android settings set Termux battery use to Unrestricted/Don't optimize and
allow background activity/data. Keep the phone powered and connected during
market monitoring.

For reboot recovery, install the official Termux:Boot companion app from the
same trusted source as Termux, open it once, then run:

```sh
./termux_backend.sh install-boot
```

Remove reboot startup with:

```sh
./termux_backend.sh uninstall-boot
```


## Official NSE adjusted history without Upstox

The backend now builds daily equity history directly from official NSE bhavcopy
archives and the NSE corporate-actions feed. It works in bounded batches and
stores progress in `~/nse-fundamentals-data/runtime/nse_eod_history.sqlite`.
Upstox is not read by this collector.

Only completed daily bars are accepted. Bonus, split and consolidation factors
are applied to earlier OHLC/volume. Rights issues, demergers and other complex
price-reset actions are fail-closed: affected symbols are excluded until an
independently verified adjustment is available. `market_ohlcv.csv` is published
only after the requested archive and corporate-action ranges are complete.

The daily worker advances a small batch automatically. For a larger bounded
catch-up run:

```sh
python swing_nse_history.py --days 120 --max-minutes 25
cat ~/nse-fundamentals-data/runtime/nse_eod_history_status.json
```

## Non-Upstox maintenance

While Upstox is deferred, the phone can still run bounded official-source recovery
for annual/quarterly fundamentals and sector classification. The helper stops the
worker first so report files are not written concurrently, then restarts it.

```sh
./termux_backend.sh maintenance
```

This command does **not** use an Upstox token, does not enable SMS, and never
places orders. It writes progress to `~/nse-fundamentals-data/runtime/maintenance_status.json` and details
to `~/nse-fundamentals-data/runtime/maintenance.log`. NSE 403/429 or missing official historical evidence
remain visible blockers; they are never converted into passes.

## Daily commands

```sh
./termux_backend.sh status
./termux_backend.sh restart
./termux_backend.sh stop
tail -n 100 ~/nse-fundamentals-data/runtime/termux_backend.log
tail -n 100 ~/nse-fundamentals-data/runtime/analysis.log
```

After a GitHub update:

```sh
./termux_backend.sh stop
git pull
python -m pip install -r requirements.txt
./termux_backend.sh start
./termux_backend.sh status
```

F&O remains research/paper only. Real trades require explicit user confirmation
and are not sent by this software.
