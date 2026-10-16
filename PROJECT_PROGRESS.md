# Project progress — 16 October 2026

## Completed in this update

- Offline adjusted-history CSV import, with exact-byte SHA256 matching and supplied source/adjustment attestation checks. No broker account is needed. This validates the input structure, not provider truth.
- Shared validation for imports and actual backtests: missing symbols, invalid OHLCV, duplicate daily bars, malformed/intraday dates and future bars are rejected. Current-day bars remain excluded.
- Importing new history invalidates old reports/plans. Failed screening removes old candidates and their manifest.
- Dashboard hides candidate files without a fresh manifest or when any linked historical, fundamental or backtest input changes. Plans expire from display after 24 hours; all remain conditional research.
- `python swing_readiness.py` reports local missing inputs, history coverage, matching-data technical pass count, worker freshness and unresolved operational dependencies without contacting a broker. Dashboard shows the last check timestamp.
- GitHub test workflow runs the complete unittest suite.

## Verification

Local full suite: 68 tests passed, including synthetic end-to-end history → backtest → zero eligible candidates. Existing authentication, risk, SMS and fundamental source tests also pass. Synthetic fixtures establish software behavior only; no investment performance or real SMS delivery was tested.

## Current evidence and remaining work

Latest committed fundamental reports are dated 15 October 2026:

| Area | Evidence | Remaining work |
|---|---|---|
| FY2024–25 | 1,349 annual data-quality passes | Complete full multi-year/sector criteria |
| FY2025–26 | 1,528 annual data-quality passes | Complete full multi-year/sector criteria |
| FY2022–23 | 1,277 unverified index candidates; 0 verified historical XBRL rows | Verify original audited consolidated annual facts; 470 symbols still lack a candidate/review |
| FY2023–24 | 1,400 unverified index candidates; 0 verified historical XBRL rows | Verify original audited consolidated annual facts; 347 symbols still lack a candidate/review |
| Quarterly | 1,750 verified records; 0 qualified comparisons | Obtain eligible full annual screens and qualified comparisons |
| Final shortlist | 0 of 1,747 screened | Resolve data/source gaps without weakening filters |
| Adjusted OHLCV | Import and validation implemented; real dataset unavailable in this checkout | Obtain authorized adjusted price AND volume history and validate source evidence |
| Backtesting | Fixed-rule engine and regression tests | Run on real history; point-in-time fundamentals/universe, portfolio and forward paper validation remain incomplete |
| Upstox | Read-only polling code | User account activation pending; token and real feed verification deferred |
| SMS | Guarded transport code | Provider/template setup, event orchestration and delivery-receipt reconciliation |
| Cloud | No deployment verified | Configure persistent hosting, private settings and operational monitoring |

No orders are placed. No future success probability is calibrated. Having a token or passing unit tests does not make the system live-ready. Local Windows files are not automatically updated by a GitHub commit.

Progress in this file is a dated snapshot, not a background service or recurring notification.
