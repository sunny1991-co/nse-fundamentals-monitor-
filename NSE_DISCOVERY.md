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


## Official audited annual XBRL catch-up (17 October 2026 implementation)

The daily Termux research maintenance runs a bounded 25-filing catch-up
through the existing nse_annual_history.py verified XBRL extractor:

    cd ~/nse-fundamentals-monitor-
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_mainboard_discovery.py
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_annual_history.py --include-discovered --limit 25

This opts into official current NSE mainboard EQ securities verified by the
fresh discovery catalog in addition to the previous fixed FY baseline.
Discovery older than 48 hours, invalid EQ/ISIN data, missing verified SME
source or truncated catalog causes an error, not an unverified fallback.

The extractor filters NSE official filing-index records by FY March 31,
audited, consolidated, publication date, then verifies the XBRL reporting
basis and annual contexts. It appends only verified records to
annual_historical_results.csv in the private NSE_DATA_DIR, with bounded
progress at runtime/nse_annual_discovered_catchup_status.json. Previously
verified data is cached; blocked documents remain retryable. The original
FY2425.csv and NSE_FY2025-26_Consolidated_Fundamentals.csv stay unchanged.

The current four-year fundamental quality checker already merges verified
historical facts, so after enough source-verified FY 2022-23 through FY 2025-26
records are collected, the new symbols may be evaluated by the SAME STRICT
annual rules. Not all companies will have four audited consolidated fiscal
years (particularly newer listings). No missing financial field is imputed;
source access blocks and unfinished records do not count as PASS.

**Limits:** A batch of 25 source filings is not 25 complete companies. The
official NSE API/index can block or lack filings. No historical stock OHLCV
backfill or real trading is added by this step. Do not equate a fundamental
PASS with a tested entry/stop/exit strategy or ready broker order.


## FY 2022-23 / FY 2023-24 public annual PDF pilot (20MICRONS only)

The previously audited legacy XML check correctly rejected its January-March
quarter contexts as FULL FY contexts. Independent annual-report PDFs on the
20 Microns *company investor-relations* page are separate primary-source
candidates; this pilot does not relabel the rejected XML as annual.

Company's official page: https://www.20microns.com/financial-report
Exact curated PDFs:
- FY22-23: https://fb949d44010d40db156a-660543cdef2fb4867335bf5294dccba6.ssl.cf2.rackcdn.com/financial_report/1689584643.pdf
- FY23-24: https://fb949d44010d40db156a-660543cdef2fb4867335bf5294dccba6.ssl.cf2.rackcdn.com/financial_report/1719920352.pdf

Termux, one-time lightweight optional PDF text parsing dependency:
    python -m pip install "pypdf>=5,<8"

After updating the GitHub repo and refreshing the official mainboard snapshot:
    cd ~/nse-fundamentals-monitor-
    git pull --ff-only
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_mainboard_discovery.py
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_annual_report_pdf_pilot.py --year both

Private persistent output:
    $HOME/nse-fundamentals-data/runtime/annual_report_pdf_pilot_status.json
    $HOME/nse-fundamentals-data/runtime/annual_report_pdf_pilot/

The pilot:
1. Requires current fresh official NSE EQ identity and ISIN for 20MICRONS.
2. Permits only the two exact reviewed company-linked PDF URLs, no user URL,
   redirects, alternate domain, broker token, or exchange login.
3. Limits response to 32 MiB and PDF page count to 400; checks cover year,
   SHA256 integrity, and extracts page numbers with likely consolidated
   auditor, balance sheet, profit/loss, and cash-flow headings.
4. Saves PDFs and their provenance/section receipt *locally*, never in GitHub;
   successful re-runs use a hash-validated cached PDF.
5. Always leaves extracted company financial metrics unverified. Real table
   numbers require a separate exact fiscal-year, consolidated statement,
   auditor opinion, reporting-unit (INR/crore/lakh), line-item, and source-page
   review, with duplicate/revision and FY comparatives handled explicitly.

IMPORTANT: this does NOT fill FY2022-23 or FY2023-24 into
annual_historical_results.csv. No future-company bulk PDF ingestion or
unattended review is implemented; manual source and row validation still
needed. No strict fundamental/company trading eligibility is changed.

## Automatically monitored new-company technical radar (17 October 2026)

The **existing supervised Termux history worker** now runs additional
bounded jobs on each cycle after its original NSE history collection:
- nse_mainboard_history_backfill.py --days 60 --max-minutes 12
- nse_mainboard_technical_radar.py

The new backfill writes to a **different** private SQLite database
(runtime/mainboard_raw_research_history.sqlite). It saves **all** original
NSE EQ bhavcopy rows, not just the original 1,254-stock fundamental universe,
so new EQ companies are already present in earlier downloaded archive days.
It processes the newest missing complete-market days first, then gradually
reaches back to January 2020. Progress is resumable after power/network
interruptions, day-completion and provenance are recorded transactionally,
and NSE HTTP 403/429 is never bypassed or silently counted as success.

Only **current NSE mainboard** constituents from the separately validated
NSE Equity/SME catalog enter the scanner. The radar monitors bar counts,
last completed close, 63-session raw return, 50/200-day trend, previous
30-session raw high, abnormal volume and 20-session raw turnover.
Existing four-year verified annual history and strict-screen statuses are
shown beside technical observations, not retrospectively assumed.

IMPORTANT: This archive is **NOT adjusted for bonus, splits, mergers,
demergers, dividends, rights or other corporate events**, and the current
mainboard list is not proof of historical listing membership. Apparent
breakouts caused by corporate-action changes can be false. Therefore the
entire radar remains *candidate discovery/research only* and is never passed
to the pre-existing adjusted-OHLCV, independent TRI, strict fundamental,
backtest or owner-YES/NO paper proposal pipeline. There is no return or
profitability guarantee and zero real orders.

Reports in the private NSE_DATA_DIR:
- runtime/mainboard_raw_research_history_status.json
- runtime/nse_mainboard_auto_research_radar_status.json
- nse_mainboard_auto_research_radar.csv (all current mainboard members)

The Termux supervisor must actually be **running** for scheduled loops; no
script can run while Android is powered off or the process is stopped.
Check with:
    cd ~/nse-fundamentals-monitor-
    git pull --ff-only
    bash termux_backend.sh restart
    cat ~/nse-fundamentals-data/runtime/nse_mainboard_auto_research_radar_status.json

**Outstanding blockers for fully hands-off audited onboarding:** 2022–23
and 2023–24 full-year source documents must be reliably discovered, tied
to symbol/ISIN, audited/consolidated basis, publication date, report
comparatives, units and cash flow, and independently reconciled. The old
quarter-only original XBRL must not become annual data by relabeling it.
The separate, curated 20MICRONS PDF pilot is *review-only* and cannot be
generalized into automatically verified financial facts without more source
validation. Further needed: corporate-action-adjusted archive, correct
historical universe/backtests, independent NIFTY 50 TRI, and forward paper
validation. Strict trade eligibility is never weakened.

## Automatic older audited annual-report PDF SOURCE discovery

NSE's official corporate Annual Reports portal lists company-submitted full
annual report attachments by symbol and fiscal year:
https://www.nseindia.com/companies-listing/corporate-filings-annual-reports

The existing supervised daily Termux maintenance now tries the official NSE
Annual Reports index for up to **25** different NSE Mainboard EQ symbols per
day (weighted toward companies not yet in the original annual baseline).
Checks rotate; old and new companies are never permanently starved. It
records only FY2022–23 and FY2023–24 full-report source candidates, and
downloads at most **one** original NSE-hosted PDF per day into a private local
runtime folder, with a 26 MB per-file and 1 GiB total budget. Low disk space
or NSE 403/429 stops downloads (no access-control bypass). API changes are
shown as errors, not silently converted to guessed URLs.

Termux automatically invokes it after the official mainboard update, or use:

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_official_annual_pdf_auto_index.py --limit 25 --download 1

Private reports:
- nse_official_annual_pdf_candidates.csv
- runtime/nse_official_annual_pdf_index_ledger.csv
- runtime/nse_official_annual_pdf_collector_status.json
- runtime/nse_official_pdf_download_receipts.csv
- runtime/official_annual_report_pdf_candidates/ (PDFs by hashed URL)

This is automatic *source discovery and provenance capture*, NOT automatic
approval of financial statements. The next audit phase must separately
establish exact issuer, audited consolidated reporting basis, date/period,
units, statement values, source page, and comparative reconciliation, and
reject ambiguous scans/quarters. Only successful independently source
verified annual numbers can enter the strict four-year screen. No PDF
financial figures or new trade signals are ever fabricated by this collector.
New listings that have less than four complete reporting years remain
research-only. Neither manual owner approval nor live broker orders are
turned on automatically by any part of this system.
