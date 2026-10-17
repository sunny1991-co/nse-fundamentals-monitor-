# NIFTY 50 vs NIFTY 100 PR: research-only market context

After importing the two NIFTY 50/100 Historical **Price Return** (PR)
exports with nifty_price_return_import.py, run:

    cd ~/nse-fundamentals-monitor-
    git pull --ff-only
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nifty_pr_market_context.py

To restrict to an already known historical end date without looking
ahead (example):

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nifty_pr_market_context.py --as-of 2026-10-15

This tool verifies each private local CSV's SHA-256 metadata receipt,
parses canonical OHLC rows, matches the two index histories on **exact
identical dates only**, refuses more than 10 unmatched observation dates,
and computes historical 21/63/126/252 common-session price returns,
50/200-session simple moving average state, and trailing 252-session
closing-price drawdown. It also reports how many dates did not align.

Research JSON stays private at:

    $HOME/nse-fundamentals-data/runtime/experiments/nifty-pr-market-context-v1/report.json

This context is **DESCRIPTIVE**, not an entry/exit signal, strategy
backtest, calibrated forecast, or verification that either index is a
suitable benchmark for a trading strategy. The two indices overlap
substantially by construction; their relative returns are *not*
portfolio alpha. Price-only levels exclude cash dividends, fees,
transactions, inflation and taxes. Imported CSV data is owner supplied,
not independently verified against the published original.

Existing separate price-only NSE cash equity strategy retrospective:

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_cnc_price_only_compare.py --limit 12

That requires distinct, attested, adjusted NSE company OHLCV and does
not consume these index PR reports or change its frozen signal rules.

Hard exclusions: cannot substitute NIFTY 50 PR for gross TRI, cannot
unblock strict fundamental + TRI-gated candidate signals, cannot create
live/paper orders, and does not claim that any strategy is profitable.
This is an isolated reporting tool and does not alter broker credentials
or already existing execution, fundamental or strategy modules.
