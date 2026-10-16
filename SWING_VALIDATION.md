# Swing research status

Read-only quote polling code exists, but a working live connection remains unverified. No broker order placement or calibrated future probability is implemented.

Run `python swing_technical_screener.py` after supplying market_ohlcv.csv and market_data_metadata.json. Required columns: Symbol,Date,Open,High,Low,Close,Volume; dates must be timezone-naive daily session dates. The JSON must contain source, adjustment_method, corporate_actions_adjusted=true, and sha256 matching the exact CSV bytes. These are provenance attestations: a hash alone does not establish provider accuracy. Verify split/bonus adjustments for prices AND volume before setting them. Never fabricate metadata. Today's daily bars are excluded.

At least 1260 sessions are required. Fixed technical rules are evaluated on chronological development and holdout intervals with no parameter search. Minimum completed trades: 20 development, 30 holdout; 5 in each half of holdout. Holdout profit factor >=1.5, mean net trade return >0, marked-to-market drawdown <=20%, and doubled-cost stress mean >0. Both holdout halves must have positive mean return. These thresholds are conservative design defaults, not evidence of a profitable strategy. Do not tune to the holdout and continue calling it unseen data.

The exact shared rules govern candidates and simulations: close-confirmed breakout, next-session opening fill within signal-close to +1% including slippage; no chasing. Initial stop: lower of 10-session low and close minus 2 ATR. Skip risk above 12% of entry. Sell half at 2R, remainder at 3R. After target 1, update the next-session stop to max(old stop, entry, close minus 2 ATR). Maximum hold 100 sessions. Protective stops can exit earlier than one week. Daily bars cannot establish intraday order, so a bar touching stop and target assumes stop first. Gap-down stops use opening price with slippage. End-of-sample liquidations are included in statistics but excluded from minimum completed-trade counts.

Defaults assume 0.2% all-in cost and 0.1% slippage per side, with stress doubling both. Actual taxes, brokerage and liquidity effects need provider/account-specific validation. Backtest equity assumes full allocation to one stock for measuring its strategy; this is NOT a recommended portfolio allocation. Evidence-gated capital-based position sizing exists in `research_monitor/risk.py`; account configuration and end-to-end validation remain pending.

This is a per-stock TECHNICAL backtest. Present fundamentals are never retroactively applied to historical trades. A historical fundamental-plus-technical strategy requires point-in-time filing availability and historical universe membership. Survivorship bias, portfolio correlations, benchmark comparison and prospective paper-trading validation remain unresolved. No winner probabilities or investment-readiness claims are justified yet.

Candidates additionally require a fresh current fundamental PASS and current technical signal. Missing provenance/feed or reports blocks output and removes old candidate output. Run locally only: licensed OHLCV must not be committed to the public repository. No live orders or F&O execution code is included.

Sector API errors now produce explicit unknown sectors so reports can continue. Unknown, stale or specialist sectors remain excluded; availability of official sector data is still required.

