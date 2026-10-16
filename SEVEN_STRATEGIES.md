# Seven NSE equity swing hypotheses — research only

**Implemented:** 16 October 2026. This does **not** mean profitable
backtests or an active trading deployment. Existing Breakout-v1 and
Trend-Pullback-v1 remain separate experimental baselines.

## Frozen v1 strategies

1. **Trend Following:** price above EMA50 above EMA200; close above the
   preceding 20-session high, RSI 50–75.
2. **EMA Pullback:** long-term EMA uptrend, previous close at/below EMA20,
   current close above EMA20, RSI 50–72 and normal volume.
3. **Momentum Breakout:** close above previous 55-session high, volume
   at least 1.5× 20-day average, RSI 55–80, above EMA50.
4. **VCP Breakout:** price/return-volatility contraction using only prior
   sessions, then a volume-confirmed break above previous 20-session high.
5. **Relative Strength Momentum:** strong stock versus the **NIFTY 50
   TRI** on exact matching dates (63/126-session excess returns) with a
   breakout. **Blocked without a separately attested NIFTY 50 TRI file.**
   Never substitute a stock index price index for the total-return index.
6. **Mean Reversion:** EMA200 uptrend, prior close below lower
   volatility band, current bounce and RSI 35–55.
7. **Post-Earnings Momentum:** consolidated quarterly result released
   via dated NSE primary-source filing, with YoY revenue >=10% and
   PAT >=15%, plus volume breakout. **Blocked without a verified,
   timestamped point-in-time earnings-event file.** Announcement day
   is *never* a same-day actionable signal.

Every price-only strategy shares 20-session turnover >= INR 5 crore,
positive ATR, conservative stop, <=12% stop distance and unchanged
next-day opening fill constraints. The shared engine imposes 0.2%
illustrative cost and 0.1% slippage per side, plus both doubled for
stress, next-open fills, gap-down stops, stop-first intraday uncertainty,
50% profit-taking at 2R, balance at 3R or trailing exit, and 100-session
maximum holding period.

## Ranking safeguards

The daily Termux research maintenance runs up to 40 deterministic
stocks for this seven-strategy module. An explicit `--limit 100`
comparison is possible, using the same universe and fixed rules; both
are **exploratory**. The original Breakout/Pullback pairwise experiment
also remains available.

The ranking requires **>=30 completed trades** (both normal and stress),
**>=10 distinct traded stocks**, positive mean *net* completed return
normal and under doubled costs, profit factor >=1.5 with actual losses,
and worst per-stock drawdown <=20%. Forced sample-end liquidations
are excluded from completed-trade profitability. It ranks only eligible
research candidates by stressed completed net mean; **Top 3 is at most
three, never automatically filled**. Overlapping individual-stock
returns are not realistic portfolio returns.

The retrospective data is already inspected: there is **no untouched
holdout** for a future approved strategy from this sample. Multiple
hypotheses amplify selection bias. Even an exploratory Top 3 is
**not a verified winning Top 3**. Full point-in-time universe and
fundamentals, independent future evaluation, realistic taxes and
execution, and sufficient forward paper fills are still required.
Profitability, future probabilities, and live-readiness are not claimed.

## Private-only inputs

Existing:
- `market_ohlcv.csv`, `market_data_metadata.json` (approved adjusted
  price and volume, as required by `research_monitor.history`).

Optional **never commit either file or metadata to public GitHub**:
- `nifty50_tri_history.csv`: `Date,Close` (daily NIFTY 50 TRI level).
  `nifty50_tri_history_metadata.json`: `type` =
  `OFFICIAL_NIFTY_50_TRI`, `sha256` of exact CSV bytes,
  `operator_source_checked` = `true`, `index_name` =
  `NIFTY 50 TRI`, `official_source_url` with official NSE or
  Nifty Indices HTTPS hostname.
- `official_earnings_events.csv`: `Symbol,Announced_At_IST,
  Revenue_YoY_pct,PAT_YoY_pct,Official_URL,Document_SHA256,Basis,
  Verified_Primary_Source`. Announcement timestamps must have the
  explicit `+05:30` offset; basis must be `CONSOLIDATED`, filing URL
  an HTTPS primary NSE host, and `Document_SHA256` a 64-digit hex
  digest. `official_earnings_events_metadata.json`: `type` =
  `OFFICIAL_POINT_IN_TIME_NSE_EARNINGS`, exact-byte `sha256` and
  `operator_source_checked` = `true`.

An operator's file attestation is **not** independent exchange-source
verification; all this remains research-only. Missing or tampered
optional files disable the dependent strategy instead of inventing
benchmark/earnings observations.

## Running and reviewing on Termux

```bash
cd ~/nse-fundamentals-monitor-
git pull
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_seven_strategy_research.py --limit 100
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python project_status.py
```

Private output:
`$NSE_DATA_DIR/runtime/experiments/seven-strategies-v1/comparison.json`.
The project status hides any research ranking after prices, optional
sources, metadata or strategy code change until re-run.

**No code in this experiment sends real orders, enables SMS alerts, or
promotes active strategies. F&O stays paper/research only. Real equity
trades require the user's express confirmation.**
