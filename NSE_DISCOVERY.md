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


## Project Status: legacy adjusted EOD vs automatic all-mainboard raw research

After the supervised worker has run:

    cd ~/nse-fundamentals-monitor-
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python project_status.py

The old remaining_archive_days field covers only the previous
corporate-action-adjusted EOD collector. Zero remaining there does NOT
mean the distinct 2,344-stock raw research archive has finished.

Use automatic_mainboard_unadjusted_archive_days_complete and
automatic_mainboard_unadjusted_archive_days_pending separately.
automatic_mainboard_radar_state and automatic_official_annual_pdf_index_state
show distinct research phases. official_coarse_sector_reference_state may
be OK while official_sector_rows remains 0 because broad index Industry
is NOT the full official issuer-level four-tier sector mapping.

automatic_mainboard_raw_breakout_candidates counts RAW price patterns,
not validated strategy signals, trade eligibility, probability of profit,
or Groww execution permission. The new research pipeline cannot authorize
orders, modify the adjusted trading files or trigger automatic SMS.


## Automatic backfill priority and responsive Termux status

The first operator run reported all-mainboard raw history 5 sessions complete
with 1,681 expected sessions outstanding, while legacy adjusted data already
had zero missing days and over 1.3 million verified/adjusted OHLCV rows.
Previously the worker called its expensive legacy republisher BEFORE
the new-company backfill, and the raw archive could stay unchanged until that
collector's long batch ended. This was a scheduling issue, not proof of an
exchange download error.

The supervised history worker now runs bounded all-mainboard research first,
and reports MAINBOARD_RESEARCH_COLLECTING for that phase. When the prior
adjusted history already covers the most recent fully completed reviewed
NSE equity session, and daily coverage/corporate-action coverage and publish
attestations are valid, it SKIPS redundant legacy republishing. New completed
trading sessions trigger the original adjusted collector again. Unattested
statuses always run the old full strict verification. New raw archive progress
(not just old adjusted archive) drives a 3-minute catchup sleep after healthy
batches; NSE access blocks return to slower backoff rather than retrying fast.

Raw and adjusted files remain separate. The new raw archive is not corporate
action adjusted and cannot feed trading/backtests/orders; this change never
loosens fundamental, liquidity, risk or owner approval gates.

To apply the new running worker code after merging this patch:
    cd ~/nse-fundamentals-monitor-
    git pull --ff-only
    bash termux_backend.sh restart
    bash termux_backend.sh status

The first few lines of the new history_worker.log cycle will show the
automatic all-mainboard research phase before the adjusted-data phase.
A previous COLLECTING state alone does NOT prove a hung collector.


## Automatic one-minute real-time chart RESEARCH via Upstox V3

During a VERIFIED live NSE equity session, the existing 10-second swing worker
automatically runs a separate **60-second** Upstox V3 1-minute OHLCV chart
research monitor. It uses the already configured read-only
UPSTOX_ANALYTICS_TOKEN, does not require Groww's separate API plan and
does not call any broker order endpoint. The original strict, audited,
consolidated fundamentals and NIFTY TRI rules remain untouched.

Source: https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/
Upstox Analytics Token scope: https://upstox.com/developer/api-documentation/analytics-token/

Each session the monitor:
- Requires verified local current NSE mainboard+SME-excluded catalog, genuine
  EQ symbol and ISIN, and exchange open status from Upstox in addition to the
  independently reviewed session calendar.
- Chooses **at most 4 symbols** from existing raw-EOD breakout research, the
  20MICRONS pilot, and high-coverage radar. Override with a verified-only,
  comma-separated NSE_CHART_SYMBOLS in private .env when desired; a symbol not
  in the current NSE mainboard EQ catalog blocks the batch.
- Fetches OFFICIAL BROKER-PUBLISHED 1-minute instrument OHLCV (not manually
  sampled 10-second price quotes), rejects incomplete/current candles,
  wrong-day, future, duplicated, malformed or stale source candles, and
  requires 65 or more CLOSED session minutes before indicators are available.
- Calculates EMA20/EMA50, RSI14, MACD(12,26,9), volume ratio to previous 20
  bars, same-session VWAP, and nearest previous 20-minute highs/lows. Only
  displays 'BULLISH_BREAKOUT_REVIEW_ONLY' or similar explanatory patterns.
- Persists a bounded private status at:
  runtime/live_chart_research_status.json
- Displays mobile-friendly HTML/SVG candlesticks under the EXISTING MFA
  dashboard (http://localhost:8787/charts), auto-refreshing the display
  roughly every minute. Off-market displays a blocked/stale status.
- Source 401/403/429 errors trigger a 5-minute cooldown; no hidden proxy,
  credentials in reports, session bypass, fabricated candles or blind retry.

Activation after GitHub merge (new swing worker code needs restart):
    cd ~/nse-fundamentals-monitor-
    git pull --ff-only
    bash termux_backend.sh restart
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python project_status.py

IMPORTANT: This is **bounded live-session near-real-time research** on up
to 4 NSE EQ stocks each minute, NOT a free all-2,344-stock tick streaming feed,
and not a successful/profitable trading system. Analysis requires a connected,
authorized Upstox Analytics Token, a running Termux backend, and an open NSE
session. Historic and new-company raw-EOD data must not become tradable
without corporate action adjustments and full audited fundamental screens.
No real order, protective stop, owner YES/NO approval, or SMS alert is enabled
by the chart module.


## Prioritizing FY22/23 source documents for the existing complete FY25/26 set

The automatic NSE Annual Report metadata collector now **prioritizes**
companies with all six mandatory numeric baseline fields populated in BOTH
FY2024-25 and FY2025-26. The strict screen needs two earlier audited,
consolidated **full-year** reports to calculate 3-year CAGR, ROE, ROCE and
cash-flow quality. There is no value in treating an FY22 or FY23 Q4 filing
as a complete full fiscal year.

This priority changes the order in which old archived annual-report URLs
and PDFs are collected; **it does not classify those six baseline numbers as
independently audited or verified**. The original source quality checks,
symbol/ISIN-linked official mainboard list, report provenance, HTTP block
handling, 26 MiB PDF size cap, 1 GiB private storage cap and 512 MiB
free-disk safety reserve remain in effect.

Never-checked new listings are still visited; if previously checked, all
companies rotate by oldest check time. During each scheduled daily
maintenance the source collector attempts up to 25 verified NSE companies
and privately archives up to TWO original official older-year PDF candidates.

After updating, the status summary will include
prioritized_two_year_six_core_company_sources_selected.
The PDFs remain evidence for independent audited-consolidated numeric
reconciliation and do NOT feed annual_historical_results.csv,
strict fundamental pass, strategy backtest or live broker orders.

## Automatic archived annual report PDF page review (read-only)

The automated daily NSE Annual Reports source collector is followed by
nse_annual_pdf_section_review.py --max-files 2 in Termux maintenance.
It does NOT fetch unverified new websites. It uses ONLY existing
SHA-256-verified privately archived original NSE annual-report candidates
whose official FY, symbol, ISIN and URL match the current official
mainboard+SME-excluded catalogue and the NSE report index.

A bounded pypdf **partial text scan** finds potential physical PDF pages for
(1) independent consolidated auditor's report, (2) consolidated balance
sheet, (3) consolidated profit & loss, and (4) consolidated cash flow.
The first four PDF pages are scanned for the apparent financial year.
A missing page is explicitly reported as "INCOMPLETE ... REVIEW"; a found
page is a candidate, not proof of audited reporting or a correct monetary
fact. Unreadable/ambiguous PDFs remain in review and are never auto-OCR'd.

Output (private NSE_DATA_DIR):
- nse_annual_pdf_section_review.csv, one row per reviewed PDF/hash
- runtime/nse_annual_pdf_section_review_status.json, bounded batch status

Example after update:
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_annual_pdf_section_review.py --max-files 1

The old company FY2022–23/FY2023–24 20MICRONS PDF pilot is a
separate curated source. This new NSE-only intake is still a FIRST
STAGE of the broader automated workflow. Full audited/consolidated numeric
extraction, independent reconciliations, units, comparable fiscal periods
and source quality verification still require a separate fail-closed step.
It NEVER appends facts to annual_historical_results.csv, approves strict
four-year fundamentals, promotes a trade strategy, trades, or alerts.

## Unattended project health doctor and optional AI diagnosis

The EXISTING Termux Supervisor owns the worker processes and now runs a
separate bounded `research_monitor.auto_doctor` health diagnostic about
every **two minutes**. No extra market subscriptions are required.
It observes:
- market worker's verified exchange-calendar and quote research loop
- separate official adjusted and all-EQ raw EOD history worker
- NSE annual/quarterly, sector, and PDF-section daily research worker
- private Upstox vendor-reference worker
- MFA-only localhost Dashboard via the fixed /healthz endpoint
- known NSE API blocks, archived PDF source-review blocks and free disk

**Safe repair actually performed automatically:** if a child is confirmed
stale in two spaced consecutive health checks AND its supervised process has
existed at least 10 minutes, terminate only that known subprocess group.
The independently tested Termux Supervisor restarts it on the next cycle,
retaining stored research and its own cooldown. At most **two automatic
stale-worker restarts per component in any six-hour window**, each at least
30 minutes apart. Process crashes were already auto-restarted by Supervisor.

A legitimately long market analysis receives a 3-hour grace period;
history, maintenance and vendor jobs get their separate conservative time
budgets. NSE 403/429 API denials, missing or stale source facts, issuer/sector
ambiguity, bad archival hashes and insufficient disk **never** trigger a
blind restart, deletion, authorization bypass, fabricated annual values or
automatic live trading. Those are persistent explicit issues needing
operator/data-source/code review. The doctor does NOT run git pull or execute
unreviewed AI-generated commands. It cannot fix an offline phone or restore
network/provider access by itself.

Private machine-readable outputs:
- runtime/project_auto_doctor_status.json — last scan and incident codes
- runtime/project_auto_doctor_recovery_journal.json — capped durable repairs
- project_status.py — includes automatic_project_doctor_state and issue count

**Optional actual OpenAI model diagnostic (NOT enabled by default)**:
Set NSE_AI_DOCTOR_ENABLE=1 and an **independent paid OpenAI API**
OPENAI_API_KEY locally in your private environment only after explicit
user cost/privacy acceptance. ChatGPT Plus does not supply an API key.
It sends **only sanitized issue types and component labels**, never raw
financial datasets, phone paths, account details or bearer tokens, to the
Responses API with store=false. Limited to one attempt per hour and a
six-hour repeat cooldown for the same incidents. It can write concise
advice but does NOT generate/exe cute arbitrary terminal commands, GitHub
merges, brokerage instructions or any privileged action. All actual
automated repairs remain deterministic, pretested and capped.

To install new supervisor code after PR merge:
    cd ~/nse-fundamentals-monitor-
    git pull --ff-only
    bash termux_backend.sh restart
    cat ~/nse-fundamentals-data/runtime/project_auto_doctor_status.json

Under Android process kill, powered-off phone, loss of data connectivity or
unavailable external APIs, an on-device guardian cannot operate. Truly
always-on operation would require separate cloud hosting and credentials.

### First project doctor status after supervisor restart

The first self-healing health scan now runs immediately after all supervised
Termux subprocesses start, writing
`runtime/project_auto_doctor_status.json` without a two-minute initial
blind spot. Checks then repeat at the normal two-minute cadence. The first
scan reports worker startup as a grace period, NOT a stuck worker; the
10-minute per-process safety delay and repeated health-check confirmation
still apply before any automatic restart.

## FY22/FY23 audited annual PDF locator precision (adaptive bounded scan)

The first NSE archived-PDF candidate locator sparsely sampled late pages, so
a small group of four real consolidated statement headings **between** its
samples could be incorrectly marked missing. The section-review v2 adaptive
scanner preserves the same strict 90-page extraction cap, but searches a
contiguous likely late-report financial region (up to 50 pages) after at most
24 sparse samples, then checks neighbors of headings when available.

Already archived PDFs with a v1 review will be safely **re-examined** by the
v2 versioned receipt while retaining both immutable source-hash records.
Regression cases simulate a 280-page official annual report whose auditor,
balance sheet, P&L and cash flow headings all fall between sparse samples,
verify it remains READ-ONLY and confirm no strict fundamental PASS/order.

The search is still intentionally partial: other report layouts, images,
missing text extraction, standalone/consolidated ambiguity, period, monetary
unit and auditor scope must be independently verified. Locating a likely
section is never evidence of an audited numeric financial fact.

## Critical FY2023-24 20MICRONS PDF page-index false positive

Verified the exact original [NSE archived FY23-24 20MICRONS PDF](https://nsearchives.nseindia.com/annual_reports/AR_24150_20MICRONS_2023_2024_20062024104857.pdf)
(281 physical pages). The earlier section locator reported all four
candidate financial sections at **physical PDF page 200**. This is a
**consolidated-section INDEX** listing four later reports, not four actual
statements. The actual PDF locations were identified as:
- Physical PDF page **201**: Independent Consolidated Auditor's Report
- Physical PDF page **209**: Consolidated Balance Sheet
- Physical PDF page **210**: Consolidated Statement of Profit and Loss
- Physical PDF page **211**: Consolidated Statement of Cash Flow

v3 page section locator now requires independent *statement-local* evidence:
auditor's explicit 'we have audited' and basis/opinion language; a balance
sheet with total assets and equity/liabilities; a P&L with operations revenue
and a profit row; and a cash flow statement with actual operating + investing
activity rows. Each of the four candidate sections must reside on distinct
physical pages. Page-index headings and auditor narrative lists are rejected
without supporting statement rows.

Already processed v2 receipts automatically get a **v3 source-preserving
re-review**, and synthetic unit tests reproduce the exact false-positive
20MICRONS layout. Findings are still bounded partial text locations, NEVER
attested numeric facts, and the strict NSE XBRL/paper/real-order gates remain
unchanged. Other companies' statement formats might require separate
review before this locator can reliably identify their pages.
