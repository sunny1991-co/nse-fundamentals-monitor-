# Regime + Momentum Confirmation v1 — Original Research Hypothesis

**Created/frozen:** 16 October 2026. **Status:** EXPLORATORY; never a trading
recommendation. This tests a new selection hypothesis after the original seven
rules and a custom dip-reclaim were negative in previous 100-stock experiments.
Its first historical test cannot certify a forward edge, and the rules must
not be retroactively softened to make them pass.

## Motivation

An Indian equity price-momentum literature reports stronger momentum among
more liquid shares, but momentum is exposed to crash risk. We therefore *test*,
not assume profitable, the combination of market breadth, relative strength,
confirmation breakout, normal volatility, liquidity and fee-inclusive position
sizing. These rules do not imply a high probability of profit.

## Frozen signal rules, EOD only

An adjusted equity passes only when all are true at its completed daily close:

- Equal-weight breadth of the already-eligible **history** universe is at
  least **55%** above its own 200-day EMA, with **100+** valid stocks.
- 126-day price momentum for the stock is at least **+10%** and among the
  top **30%** of 126-day momentum observed on *that same session* across the
  history-eligible universe, with **100+** valid comparable stocks. Missing
  date/source, breadth or rank = no signal, never substitute zero or fill.
- Close is above EMA50, EMA50 above EMA200 and EMA200 is above its value
  20 sessions earlier.
- Close exceeds the highest **previous 15-session** high. Today's high is
  excluded from the historical breakout threshold.
- Today's volume is at least its 20-day average, and trailing 20-session
  traded-value average is at least **₹5 crore/day**.
- RSI14 is **52–75**, ATR14 is **1.2%–6.5%** of the close.
- Current stop is positive, **2.5%–10%** below the close. Existing stop
  convention: lower of trailing 10-day low or close minus two ATR14.
- Previous 20-session daily-return volatility is no more than **1.05x**
  previous 60-session volatility; both windows exclude today's return.
- There are no live orders or alerts.

## Position, entry and exit

- Price signals are known only **after the daily close**.
- Simulated next-session open must be within **[signal close, signal close
  +1%]** after assumed buy slippage; otherwise the entry is skipped.
- Hypothetical independent stock account = **₹100,000**; buy integer shares
  sized to an initial-stop **no-gap maximum MODELLED loss of 1%** of available
  account cash including estimated delivery fees and slippage. A real gap can
  exceed 1%, as can actual charges or incorrect source adjustments.
- Execution uses the already reviewed stop-first backtester: initial stop,
  half position at 2R, remaining at 3R or trailing stop, 100-session maximum
  hold, and sample-end liquidation separately labeled.
- Base test assumes **0.1% slippage per side** and published Groww delivery
  fee model (brokerage, STT, stamp, exchange, SEBI, IPFT, DP, GST). A double
  cost/stress scenario uses **0.2% slippage per side** and doubled estimated
  charges. Actual past tariffs, fills and personal taxes are not known.

## Run on the user's PRIVATE Termux price history

```bash
cd ~/nse-fundamentals-monitor- && git pull
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_regime_momentum_research.py --limit 100
```

Includes *this* hypothesis plus existing `vcp-breakout-v1` for comparison on
the same fixed first 100 hashed symbols. Relative momentum and breadth are
calculated using *all currently history-eligible symbols*, not only 100
sampled ones. Saves private detailed report:
`~/nse-fundamentals-data/runtime/experiments/regime-momentum-research-v1/comparison.json`.

Output includes completed (non-censored) trades, trades stopped, modelled fee
stress, win/loss, profit factor and worst *single stock* mark-to-market
drawdown, **not** a realistic multi-asset portfolio return.

### Critical research weaknesses and promotion guard

- This experiment was specified after the older historical window had
  already been inspected; performance here is **exploratory only** and cannot
  be called an untouched out-of-sample validation.
- Today's active, 1260-bar companies are historically **survivor-biased**.
  Breadth and ranks use only same/past *prices* but historical membership
  is not point-in-time. Need verified NSE mainboard constituent/universe history.
- Historical point-in-time FY/quarterly audited consolidated fundamentals and
  earnings release timestamps are missing; the model is **price-only**.
- No causal evidence of durable profitability exists. Before deployment:
  independently verified data, different new sessions, forward paper tracking,
  position portfolio limits/correlation constraints and actual broker fees
  must be tested, without parameter retuning.
- Auto-orders, alerts, active strategy promotion and candidate shortlists
  are **disabled**; `verified_profitable_strategies=0` always.

## Academic context (not a profit guarantee)

- **Momentum, reversals and liquidity: Indian evidence**, journal study
  based on BSE history 2000–2021:
  https://www.sciencedirect.com/science/article/pii/S0927538X23002640
- **Momentum and downside risk**, evidence of momentum losing money in bad
  market states:
  https://www.sciencedirect.com/science/article/pii/S0378426616300292

These publications motivate a hypothesis, not forecast performance on the
user's NSE stocks or future live returns.
