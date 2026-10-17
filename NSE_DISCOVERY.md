# Daily NSE mainboard discovery — research only

The original FY 2024-25 / FY 2025-26 company set is not the discovery boundary.
This module does NOT enlarge trading eligibility or relax the strict screen.

## Sources
- Official NSE equity securities list (EQ series only): https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
- Official NSE SME list (mandatory exclusions by symbol and ISIN): https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv

Both lists must be downloaded and validated in the same run. HTTP 403/429,
HTML, missing SME list, broken CSV or implausible size changes block refresh.
The previous verified catalog remains unchanged. A download timestamp does NOT
prove the upstream source's last publication time.

## Termux

After merging the PR:

    cd ~/nse-fundamentals-monitor-
    git pull
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_mainboard_discovery.py
    cat ~/nse-fundamentals-data/runtime/nse_mainboard_discovery_status.json

The existing daily supervised research maintenance now invokes discovery
outside regular exchange hours before other financial screenings. It requires
no Upstox or Groww credentials.

The persistent research-only results are:
- nse_mainboard_discovery_catalog.csv (all verified current mainboard EQ rows)
- nse_mainboard_discovery_queue.csv (not in current strict final shortlist)
- runtime/nse_mainboard_discovery_status.json (source hashes, counts, errors)

On first run every present stock is new relative to the absent prior snapshot.
On later successful runs New_Since_Prior_List identifies true additions/ISIN
changes. OUTSIDE baseline stocks remain watch-only. None is automatically
assigned historical returns, successful strategies or permission to trade.

## Future onboarding blockers

1. Identify symbols/ISINs using official lists (implemented).
2. Automatically collect and verify historical audited consolidated annual
   and quarterly NSE filings for each outside-baseline company (not yet done).
3. Backfill sufficient corporate-action-adjusted per-stock OHLCV across
   historic archive days for new symbols. Existing official EOD collector
   marks prior days processed with a previously filtered symbol set; simply
   adding a new target symbol would silently omit older daily bars.
4. Apply strict multi-year financial quality, daily liquidity, unbiased
   backtesting, benchmark and forward paper validation before signaling.

This feature does NOT modify original annual CSVs, historic database,
market_ohlcv.csv, final fundamental shortlist, broker settings,
swing_watchlist.json, candidate signals, paper positions, SMS or orders.
Automatic real orders and SMS approval remain OFF.

Regression tests: python -m unittest test_nse_mainboard_discovery -v


## Daily evidence-gaps report (added 17 Oct 2026)

The supervised research maintenance runs nse_discovery_evidence_gaps.py
immediately after successfully checking the official mainboard/SME catalog.
It verifies that the official catalog was fetched within the last 48 hours,
counts the locally present FY 2024-25 / FY 2025-26 rows, references only
existing strict annual/final-screen booleans, and lists any locally reported
corporate-action-adjusted price coverage as **unattested**.

Output: nse_mainboard_evidence_gaps.csv in the persistent NSE_DATA_DIR and
runtime/nse_mainboard_evidence_gaps_status.json. This identifies exactly which
outside-baseline companies still need audited filings. It does **not** fetch
missing fundamental documents, backfill old OHLCV, infer returns or expand the
tradable watchlist. Every Trade_Eligible value remains false.

Manual data-only check:
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_discovery_evidence_gaps.py

Insufficient or stale NSE list evidence blocks publication and preserves the
last report. The original NSE annual baseline, historical database and strict
trading signals remain untouched.
