# RS Pullback Recovery v1 — NSE mainboard equity, research only

**Designed 17 October 2026.** This long-only swing hypothesis seeks
strong six-month stock leaders that temporarily dip toward their rising
20-day exponential moving average, then close back above it with fresh
volume. It complements an existing *breakout* hypothesis: the entry happens
on a **recovery from a pullback**, not at the previous 30-session high.

## Frozen rules (do not tune against the observed historical sample)

1. Market filter: official operator-attested **NIFTY 50 total-return index
   (TRI)** level above EMA200; TRI EMA50 above EMA200; EMA200 rising over
   20 sessions; benchmark 126-session return positive. Exact session date
   required; a missing level blocks that day's signal.
2. Leadership: stock close above EMA50, EMA50 above rising EMA200; stock
   126-session return at least **12%**, and at least **8 percentage points**
   above exact-date NIFTY 50 TRI return.
3. Controlled dip: preceding **8 sessions' lowest low** within 1% above
   yesterday's EMA20 and no more than 3% under yesterday's EMA50. Previous
   close below yesterday's EMA20; current close reclaims EMA20 and exceeds
   yesterday's completed session high, without being more than 6% over EMA20.
4. Selling-volume drying: preceding 5-session volume average <=90% of the
   preceding 20-session volume average; recovery-day volume >=110% of that
   preceding 20-session average. Prior 20-session average turnover at least
   **₹5 crore/day**.
5. RSI14 48–69; ATR14 1–6% of close. Preliminary reference stop is the
   tighter of preceding 8-session low and close−2×ATR (the higher stop),
   with an initial reference-distance 2–9% of close.
6. Research execution model: at the **next completed session's open** only
   if an illustrative fill including slippage is between the signal close
   and +1%; otherwise skip. Model <=1% of a fictitious independent ₹1 lakh
   stock account lost at expected initial stop **including Groww delivery
   fees**, with no guarantee on gaps. Take roughly 50% at 2R, rest at 3R,
   stop-first on intraday bars, post-target trailing and time exit by 100
   completed sessions (roughly 1 week to 5 months in intended use).
7. NSE cash EQ only. **No futures, options, margin or shorting.**

## Research protocol

Run only after the code is merged:

```bash
cd ~/nse-fundamentals-monitor- && git pull --ff-only
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_rs_pullback_research.py --limit 12
```

### Official NIFTY 50 gross TRI acquisition (separate source review required)

The official NSE Indices website offers historical "Total returns Index Values"
for NIFTY 50: https://www.niftyindices.com/reports/historical-data

The source download endpoint is
`https://www.niftyindices.com/Backpage.aspx/getTotalReturnIndexString`.
It provides **TotalReturnsIndex** gross TRI, separately from the price index
and **NTR_Value** (net-of-withholding total returns). No vendor substitute.

1. Get a **quarantined, unreviewed** official NSE Indices source file. Nothing
   is added to the strategy's trusted benchmark by this action:

   ```bash
   cd ~/nse-fundamentals-monitor- && git pull --ff-only
   NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nifty50_tri_official_import.py --fetch
   ```

2. Compare the returned index name, dates and a few levels with the NSE
   Indices official historical *Total returns Index Values* page for NIFTY 50.
   Check the **gross TRI**, not spot/price NIFTY nor the NTR column. Source
   HTTP 403/429 and redirects are not worked around. A candidate only
   reports `OFFICIAL_CANDIDATE_READY_UNREVIEWED`; the download's HTTPS origin
   is not itself an independent audit.

3. **Only after the owner has really checked the source**, explicit
   manual attestation installs it:

   ```bash
   NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nifty50_tri_official_import.py --install-reviewed
   NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_rs_pullback_research.py --preflight
   ```

   It writes `nifty50_tri_history.csv` and a hash-bound
   `nifty50_tri_history_metadata.json` and refuses to overwrite any
   existing historical benchmark. The loader still requires its original
   source-attestation contract and recognizes that operator verification
   is **not independent auditing**.

4. If `--preflight` passes, run the original historical comparison again.
   Even a positive backtest result is retrospective and cannot authorize
   any BUY signal or broker order.

The importer does not enable live trading, make an operator attestation
implicitly, synthesize missing TRI sessions, or alter the portfolio.

### If the backtest reports `SOURCE_REQUIRED_OR_INVALID`

Start with the **read-only** source preflight; it does not conduct a backtest
or generate any trading recommendation:

```bash
cd ~/nse-fundamentals-monitor- && git pull --ff-only
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_rs_pullback_research.py --preflight
```

The output identifies `blocked_stage`, a fixed `reason_code`, and any
missing **public filename** (never private paths or raw exception messages).
Possible cases:

- `HISTORY_FILES_MISSING`: adjustment-attested NSE equity OHLCV CSV and/or
  `market_data_metadata.json` are not in the selected `NSE_DATA_DIR`.
- `TRI_FILES_MISSING`: official NIFTY 50 **Total Return Index**, not NIFTY
  price index, CSV and/or its separately checked
  `nifty50_tri_history_metadata.json` are absent.
- `UNVERIFIED_SOURCE_METADATA`: official benchmark data or its operator
  provenance SHA256 / source metadata is not independently accepted.
- `FROZEN_TRI_WARMUP_INSUFFICIENT`: fewer than 260 TRI observations through
  the already-inspected 2026-10-16 retrospective cutoff.
- `NO_STOCK_WITH_1260_FROZEN_HISTORICAL_BARS`: no stock has at least
  1260 adjusted daily bars inside that retrospective sample.

Do **not** create fabricated benchmark values, copy spot NIFTY prices
into TRI, re-label a provider quote series as official, or edit metadata
to force the preflight to pass. If a true official series is unavailable,
the three-way comparison remains blocked and the separately runnable
existing price-only exploratory comparisons remain research-only.

The program requires adjustment-attested daily NSE OHLCV and an
individually source-attested **NIFTY 50 TRI** file, each already held in
your private data directory. Missing inputs block the whole run, not just
an individual losing candidate. It tests the new rule against the original
breakout and Quality Leader Contraction Breakout on **the exact same
deterministically selected stocks and end-of-history comparison window**,
with estimated delivery taxes/DP/broker charges, normal and doubled costs,
slippage, model-stop risk breaches and drawdown.

Research report (private, NOT uploaded to public GitHub):

`$HOME/nse-fundamentals-data/runtime/experiments/rs-pullback-research-v1/comparison.json`

**Important:** The historical data were already inspected when the rule
was designed. The final 30% of dates is NOT an independent holdout.
Each stock is modeled as its own single-stock paper account, so the
reported net position return is **not portfolio CAGR** or a promise of
portfolio P&L. No historical point-in-time verified fundamentals or
index membership are available. Repeated strategy selection creates
overfitting risk. A promising comparison remains only a research lead.

## No automatic trading

The strategy is **NOT** wired into `assistant_signal_pipeline.py` or
the owner YES/NO workflow, because as of design there are zero strict
fundamental passes and no forward profitable strategy validation.
The scanner, real-order boundaries and current live eligibility remain
unchanged. A future promotion would require independently verified
current NSE consolidated annual/quarterly + sector facts, contemporary
quote feeds, broker protective CNC exits, permission and prospective
cost-positive paper validation.

Research background:
- Jegadeesh & Titman (1993), *Returns to Buying Winners and Selling
  Losers*, Journal of Finance, https://doi.org/10.1111/j.1540-6261.1993.tb04702.x
- Hurst, Ooi & Pedersen (2017), *A Century of Evidence on Trend-Following
  Investing*, https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing

These publications support the general momentum/trend premise, NOT
these numerical thresholds or performance of Indian individual-stock
pullback entries.
