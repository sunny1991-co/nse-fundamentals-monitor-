# Capital and Yes/No workflow — paper rehearsal only

Implemented 2026-10-16. This is a terminal and authenticated dashboard rehearsal, not live trading,
an automatic signal watcher, or a profitable strategy.
It never reads API tokens, calls a broker, buys subscriptions, or places orders.

## What works

- Exact, immutable long-equity proposal with strategy version, capital, entry
  band, stop, target, quantity and an explicit estimated per-side cost.
- Preference: Groww, Upstox, Angel One. These are preferences, not three installed
  execution adapters. Each proposal stays bound to its selected paper broker.
- Planned stop risk <=1% and position cost <=25% of each proposal's capital.
  These are independent rehearsals, not a shared portfolio risk manager.
- Five-minute entry/approval expiry, exact-plan digest, one Yes/No decision,
  SQLite transaction lock, restart persistence and local event ledger.
- Explicit price observations simulate entry only within the approved band;
  stop/target observations close the paper position. Gap stops use observed
  prices, so losses can exceed planned stop risk. No inferred missed fills.
- Stale/future, pre-approval, duplicate and out-of-order observations rejected.
- Live orders remain disabled regardless of Yes, capital, or strategy name.
  The digest detects accidental payload changes; it is not an authentication
  boundary against someone with write access to the local database.

## Termux

```bash
cd ~/nse-fundamentals-monitor-
git pull --ff-only
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_paper_approval.py status
python -m unittest test_paper_approval -v
```

For an explicit fictional rehearsal (TEST is not a recommendation):

```bash
export NSE_DATA_DIR="$HOME/nse-fundamentals-data"
python swing_paper_approval.py create --symbol TEST --strategy rehearsal-v1 --capital 100000 --entry-min 100 --entry-max 101 --stop 95 --target 113 --fee-fraction 0.003
```

Read the entire returned plan. Use its `id` and `digest` within five minutes:

```bash
python swing_paper_approval.py decide PROPOSAL_ID --digest PLAN_DIGEST --answer yes
python swing_paper_approval.py show PROPOSAL_ID
```

Use `--answer no` to reject. Changing capital or prices requires a new proposal
and a new decision. The 0.003 fraction above is illustrative, not a verified
Groww tariff. There is no live-feed subscription: `observe ID --price PRICE
--observed-at UNIX_TIMESTAMP` accepts only explicitly supplied paper observations.
Do not label manually replayed prices as verified broker execution or validation.

## Research and remaining execution work

Existing seven-strategy results and the additional regime-momentum experiment
have not established a profitable validated strategy. Do not promote the least
negative backtest to live use, or claim two/three winners exist. Previously
inspected historical periods are exploratory; independent forward data is needed.
Freeze candidate rules before collecting new evidence, account for actual costs,
and preserve losing results. No strategy thresholds change in this milestone.

Dashboard manual capital and Yes/No forms are now available at `/paper` behind
the existing MFA login and CSRF protection. They are separate from automatic
normalized-unit forward observations.

Remaining: verified market
feed and strategy-generated proposals; shared portfolio reservations; independent
strategy validation; broker-specific execution auth and account binding; delivery
protective-exit support; partial fills, rejected orders, duplicate submissions,
restart reconciliation, disconnect/overnight protection and kill switch testing.
A data-only/Analytics token is not proof of order permission. Mobile background
availability is not guaranteed. Broker preference must never silently move an
existing position or its exits to a different broker account.

The assistant can build and test this software, but cannot execute financial
transactions for the user. This commit does not enable any such transactions.

## Dashboard update

After pulling the update, restart with `bash termux_backend.sh restart`.
Open the existing dashboard at http://localhost:8787 and log in as usual.
Follow **Automatic experiment status / capital / paper Yes-No**. The page shows
the last recorded experiment check (not a claim the worker is currently alive),
recent manual plans, and a form for capital, prices, version and estimated fees.
Submit the form, review the exact plan, then choose Yes — paper only or No.
This does not connect the manual plan to the automatic forward research ledger.

To diagnose PLAN_INPUTS_CHANGED without rewriting or weakening evidence:

```bash
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python diagnose_plan_inputs.py
```

Send the changed/missing filenames for investigation. Do not manually regenerate
the manifest or remove its check: candidate generation must use matching inputs.
