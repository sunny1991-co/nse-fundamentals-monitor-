# Swing Assistant — Yes / No + Quantity

**Implementation added to the existing NSE mainboard-equity project on 16 October 2026.**
This is an explainable, research-only **paper workflow** and a separately
specified live-execution design. There are **no real broker order endpoints**
in this workflow. The project still has **zero verified profitable
strategies**, **zero strict NSE fundamental passes**, and therefore **no
eligible real-money stock recommendation**.

## User interaction contract

Once required financial, technical and live-data gates are satisfied,
the intended user interaction is exactly:

1. The system selects a **research candidate** and prepares the entry band,
   stop-loss, 2R and 3R targets, expected holding period, technical evidence,
   and **maximum risk-capped quantity**.
2. When an independently validated real-time NSE_EQ bid/ask falls within the
   proposed range, the dashboard shows **YES / NO** and **integer quantity**.
3. **NO** rejects permanently. **YES** plus 1…max quantity creates an
   **ARMED_PAPER_ONLY** plan, bound to its exact SHA256 plan fingerprint and a
   maximum 5-minute approval window. No repeated tick resets the timer.
4. A new fresh quote must still be inside the band; after YES the paper
   engine may simulate entry; it may not chase a gap. It then models stop
   losses, 2R partial target, 3R final target, completed-day trailing and
   100-session time exit. Stop/gap risk can exceed the budget.
5. One plan/day/stock/strategy/source is deduplicated transactionally in
   private SQLite, and each quote observation must be monotonically
   timestamped and fresh. Any unknown/missing tick is **not** called a
   successful fill or a protected holding.
6. Shared paper capital is now reserved atomically when the owner chooses YES:
   combined armed/open planned stop risk cannot exceed **3%** of the single
   paper-capital base; combined entry-high notional plus estimated buy-side
   fees cannot exceed **100%**. Active duplicate symbols and mixed paper-capital
   bases are rejected. These are simulation bookkeeping guards, **not** actual
   account buying power, broker-side stops or a guarantee against gap losses.

**Paper confirmation DOES NOT authorize a Groww order.**

## Strategy: Quality Leader Contraction Breakout v1

A fixed-rule equity **long-only** hypothesis for approximately 1 week
to 5 months (maximum 100 completed trading sessions). The rules were
defined after existing historical outcomes had been inspected, so even a
profitable historical split cannot validate it independently.

- Strict, verified consolidated annual + quarterly NSE fundamental
  shortlist; incomplete or vendor-only data excluded
- NIFTY 50 TRI exact-date trend regime: TRI above EMA200, EMA50 above
  EMA200, rising EMA200 and positive 63-session TRI return
- Stock EMA20 > EMA50 > EMA200, rising EMA200, close above EMA20
- Stock 63-session return >=10% and at least 5 percentage points better
  than NIFTY 50 TRI return
- Close > previous 30-session high, volume >=1.2× past 20 sessions'
  average, last daily close in upper 30% of the trading range
- RSI14 54–74, ATR/close 1.2%–7.0%, volatility contraction on 10
  versus previous 60 return observations, past turnover >=₹5 crore/day
- Initial stop from prior ten-session low or close−2×ATR; model
  stop distance 2%–10%; entry next session only without chasing >1%
- Whole-share quantity constrained by modeled <=1% of research
  capital at planned stop and <=25% of capital in one name;
  under actual open gaps this **does not guarantee** 1% loss
- Target 1 at 2R, target 2 at 3R; raise trailing stop after partial
  completion, time exit after 100 sessions
- Indicative Groww CNC delivery fees, DP charges, extra slippage and
  double-cost stress are modeled in `swing_quality_leader_research.py`

**This is not a trained/calibrated 'highest-probability' predictor.** 
No projected win rate or guaranteed profit will be displayed.

## Implemented files

| File | Function |
|---|---|
| `research_monitor/quality_leader_rules.py` | Causal frozen technical/relative-TRI setup and risk plan |
| `swing_quality_leader_research.py` | Frozen historical benchmark comparison, illustrative Groww fees and 2× stress |
| `research_monitor/assistant_signal_pipeline.py` | Strict fundamentals + current adjusted EOD + NIFTY 50 TRI watchlist; read-only quote range matching and paper ticks |
| `research_monitor/assistant_trade_queue.py` | SHA-bound YES/NO + quantity, 5-min expiry, journaled paper state machine, stop and target |
| `research_monitor/assistant_dashboard.py` | MFA/CSRF-protected `/assistant` mobile dashboard with exactly YES/NO and quantity |
| `research_monitor/assistant_eod_reconcile.py` | Post-entry-session adjusted OHLCV reconciliation with calendar gap guards, stop-first and time exit |
| `swing_ai_assistant_scan.py`, `swing_ai_assistant_eod.py` | Termux daily supervised read-only jobs |
| `swing_worker.py` | Existing verified Upstox quote stream can trigger paper approval and observe already-approved paper positions |
| `project_status.py` | Progress and trade-readiness are reported separately |
| `test_quality_leader_strategy.py`, `test_assistant_trade_queue.py`, `test_assistant_dashboard.py`, `test_assistant_signal_pipeline.py`, `test_assistant_eod_reconcile.py` | Synthetic causality, source integrity, authorization, gaps, duplicate, and execution-boundary checks |

All private inputs and research records remain in
`$HOME/nse-fundamentals-data`. All tested GitHub automation uses
**synthetic** cases, not a claim of real market profitability.

## Live Groww delivery swing trading — HARD BLOCKERS

The following **must** be completed and independently tested before
enabling any API **POST order** operation:

1. At least one independently forward-validated, cost-positive strategy,
   audited point-in-time fundamentals, source-review resolution and
   reliable NIFTY TRI + current price quote.
2. Active, permissioned Groww Trading API subscription and fresh tokens,
   linked CASH/CNC instruments, account buying power, sellable quantities,
   delivery holdings and portfolio concentration validation.
3. Real order submission with idempotency keys and durable order references;
   repeated requests must reconcile order status, trades/partial fills,
   order history and holdings before retrying.
4. Reliable **broker-side** protection for CNC holdings, with verified
   permitted GTT/bracket/stop-loss and OCO semantics for **delivery**
   positions. Groww docs show CASH/CNC **GTT**, but specifically identify
   **CASH OCO support with MIS** in OCO modification docs. Do NOT
   assume a CASH/MIS OCO is an acceptable 5-month CNC delivery stop:
   https://groww.in/trade-api/docs/curl/smart-orders
5. Verified protective-stop acknowledgement **before** the service
   considers a live holding protected; hard kill switch, lost-network
   reconciliation, daily loss budget, worst-case price limit, and
   human-readable emergency exit instructions.
6. Reliable market-session supervision, mobile notification delivery,
   authenticated approval with full plan hash and **quantity**, and
   a durable user yes/no audit trail on each new trade.

There is currently **NO live order adapter**, no real-money auto-entry,
no verified broker-side CNC stop-loss workflow, and no automated mobile
push/SMS confirmation channel. The screen runs locally at
`http://localhost:8787/assistant` after the same dashboard MFA login.
The owner does **not** need to reenter the indicator rules.

## Activate code on the Android host

Run once on the owner's Termux (does not place any real order):

```bash
cd ~/nse-fundamentals-monitor- && git pull && bash termux_backend.sh restart
```

Afterward, visit `http://localhost:8787/assistant`. If the strict
fundamental gate is still zero, the app should correctly say that no
eligible paper proposal is present rather than inventing a trade.
