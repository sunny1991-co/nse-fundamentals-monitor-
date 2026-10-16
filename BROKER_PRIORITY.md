# Two-broker NSE equity swing plan — Primary Groww, secondary Upstox

**Owner preference (16 October 2026):** Always try Groww's **market data**
first; test Upstox as a secondary data source. This does **not** mean the
software may trade on Upstox when Groww has an outage. The **only future
execution venue** will be Groww, and even that is OFF until the owner approves
a fully specified, timely plan and independent strategy/evidence gates pass.

## Current state

- **Groww** supports full NSE CASH quote data, historical data, holdings and
  API order placement, with an active paid Trading API subscription. Official
  rate is **₹499 + taxes/month**. API access tokens reportedly expire at
  **06:00 IST daily**; do not assume a permanent stored token works tomorrow.
- **Upstox** Analytics Token is free, lasts up to one year and reads market data
  but **cannot place/cancel/modify orders**. Its full account/portfolio/order
  GET endpoints require registered static IP; public market data GET doesn't.
- Current experiment outcomes: zero verified profitable, live-eligible swing
  strategies. Consequently **none** is allowed to request live orders or
  SMS trade approval. Code tests do not establish profitable execution.

Official sources:
- https://groww.in/trade-api
- https://groww.in/trade-api/docs/curl/live-data
- https://groww.in/trade-api/docs/curl/orders
- https://groww.in/trade-api/docs/curl/smart-orders
- https://upstox.com/developer/api-documentation/analytics-token/

## Existing local read-only priority check

```bash
cd ~/nse-fundamentals-monitor- && git pull
python swing_broker_priority_check.py
```

This uses **only locally stored secrets**, loads private ignored `.env`,
checks one known NSE equity example (RELIANCE; not a recommendation) from
each source with GET requests, and writes:
`~/nse-fundamentals-data/runtime/broker_priority_check.json`.

Only an up-to-date, valid NSE CASH quote can be selected:
- Both fresh and close enough (within **1%**): `GROWW` is chosen for **data**.
- Groww missing/invalid, Upstox fresh: `UPSTOX` becomes **data backup**.
- Both fresh but differ >1%: **block source selection**, investigate instruments.
- Both stale, exchange closed, or missing permissions: **no fresh quote**.
- The quote-check script **never sends orders or Yes/No approval prompts**.
  It does not replace the existing Upstox-only worker's verified watchlist
  logic with Groww trading symbols until the broker symbol ↔ ISIN mappings
  and session controls are independently verified.

Configure Upstox token privately in Termux (if not yet stored):
`python upstox_token_setup.py`

To store a separate *subscribed* Groww API token privately:
`python groww_token_setup.py` . The hidden prompt preserves Upstox config
and changes `.env` permissions to 0600. The Groww token must be renewed as
required by Groww. **Never paste either broker token in a chat or repository.**

## Future owner-only Yes/No experience

For a genuinely independent, validated swing trading strategy:

1. Freeze the strategy and eligible/mainboard universe, verified point-in-time
   audited consolidated financials, market quality and fees, then produce a
   time-limited named stock proposal from complete market data.
2. Show the owner: **stock, Groww NSE CASH/CNC account, entry, stop-loss,
   target, quantity, max notional, 1% assumed *stop-loss* risk, gap-risk
   warning, costs and current holdings. Default action: NO**.
3. Only an explicit, authenticated **YES for that exact proposal** within
   a short expiry may release ONE idempotent Groww order. Unknown timeouts
   must be reconciled with Groww order book, **not blindly retried**.
4. Confirm *actual filled quantity* before placing stops. Reconcile orders,
   fills, delivery holdings, GTT/SL status and replacement/cancellation;
   protective exits must remain correctly sized after partial fills.
5. Overnight gap, halted trading, provider outage, device sleeping, late
   broker responses and stop-limit slippage can exceed risk estimates.
   Include emergency disable and user-visible exceptions; do not imply 24/7
   guaranteed protective execution.

**Important delivery exit detail:** Groww Smart-Order OCO documentation
currently lists `CASH/MIS` and `FNO/NRML` support, **not `CASH/CNC`**.
Do not assume an intraday MIS OCO can protect a 1-week–5-month CNC swing
holding! Groww has GTT and standard stop-loss orders, but eligibility,
CNC behavior, exchange trigger/price rules, validity and live fill/state
reconciliation must be separately confirmed before any live exit automation.

The local backtesting and research system still has no active live execution
permission. Upstox data is not a guarantee that Groww quotes or orders can be
handled, and **no automatic change of trade broker** is allowed.
