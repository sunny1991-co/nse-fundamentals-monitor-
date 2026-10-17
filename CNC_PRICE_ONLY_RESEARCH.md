# NSE cash equity: three-strategy price-only CNC swing comparison

This is an **optional, stock-price-only** historical research experiment.
The verified official NIFTY 50 Gross TRI series is not available because
the publisher's internal POST returns HTTP **302** to `/Sitefinity/Login`
from Android Termux **and** the GitHub runner. The missing TRI series
remains **missing**; this program does not invent one or silently replace
it with NIFTY 50's price index.

## Read-only command

```bash
cd ~/nse-fundamentals-monitor- && git pull --ff-only
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_cnc_price_only_compare.py --limit 12
```

Uses only original already-frozen **NSE EQ swing** hypotheses:

1. Original 20-session high breakout: `breakout-v1`
2. EMA20 trend pullback reclaim: `trend-pullback-research-v1`
3. Dip then 3-session-high reclaim: `custom-dip-reclaim-v1`

Data prerequisite: private, hash-attested and corporate-action-adjusted
`market_ohlcv.csv` with its matching `market_data_metadata.json`.
Companies need **at least 1260 completed daily bars** through
**15 October 2026**. Stocks are selected in SHA256(symbol) order, not
based on profits. All strategies use the **same exact stock sample and
same retrospective window**.

Report written privately to:

`$HOME/nse-fundamentals-data/runtime/experiments/cnc-price-only-delivery-comparison-v1/comparison.json`

The runner compares original vs doubled estimated **Groww CNC delivery
charges**, DP fees and slippage, next-session no-chase entry, partial
2R/3R targets, 100-session exit, position sizing to *modeled* 1% stop
risk, intraday stop-first, actual open-gap assumptions, trade completion,
mean net **position** returns, profit factor, drawdown and risk
budget breaches. A stop fill after an overnight gap can exceed 1% loss.

**Crucial limitations:**

- Everything up to October 15 had already been inspected when the rules
  were frozen. The last 30% of the sample is **not an independent holdout**.
- Not a combined-capital portfolio: one independent ₹1 lakh paper
  account **per symbol**. No portfolio CAGR, Sharpe, calibrated win
  probability or profitable-strategy approval can be inferred.
- No audited historical **point-in-time** NSE consolidated fundamental
  qualification, historical index membership or verified 4-tier sectors.
- No NIFTY 50 TRI-relative ranking, no forward paper track record,
  no real market profit proof, no automated BUY signal or live orders.
- The permanent strict screen and RS Pullback Recovery research still
  **require original NIFTY 50 TRI** and cannot consume this technical-only
  report as proof.
- Fees are modeled; broker tariffs may differ. Confirm actual
  CNC settlement and protective exit execution before any live integration.

This research has **zero ability to place orders** or change broker
credentials, existing strict fundamental gates or daily candidate
selection. Review without treating retrospective profits as forecasts.
