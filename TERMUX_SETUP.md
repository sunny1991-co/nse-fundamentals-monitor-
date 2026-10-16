# Android / Termux backend

This mode keeps the existing equity swing research worker running on the user's
own Android phone. Upstox access remains read-only. No broker order is placed.

## What is implemented

- `termux_supervisor.py` keeps one `swing_worker.py` process alive and restarts
  it after a crash.
- It uses `termux-wake-lock` when available.
- `termux_backend.sh` provides start, stop, restart, status and one-shot commands.
- An optional Termux:Boot script can be installed for phone reboot recovery.
- Runtime state stays under the project's ignored `runtime/` directory.
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


## Non-Upstox maintenance

While Upstox is deferred, the phone can still run bounded official-source recovery
for annual/quarterly fundamentals and sector classification. The helper stops the
worker first so report files are not written concurrently, then restarts it.

```sh
./termux_backend.sh maintenance
```

This command does **not** use an Upstox token, does not enable SMS, and never
places orders. It writes progress to `runtime/maintenance_status.json` and details
to `runtime/maintenance.log`. NSE 403/429 or missing official historical evidence
remain visible blockers; they are never converted into passes.

## Daily commands

```sh
./termux_backend.sh status
./termux_backend.sh restart
./termux_backend.sh stop
tail -n 100 runtime/termux_backend.log
tail -n 100 runtime/analysis.log
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
