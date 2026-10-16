# Recover official NSE data without weakening the verification gates

## Current blocker and status

GitHub-hosted runners have intermittently received HTTP 403/429 from
`nsearchives.nseindia.com/corporate/xbrl/` and from NSE equity quote APIs.
A completed GitHub Actions run is **not** proof that new data was downloaded.
Consult `quarterly_collection_status.csv`, `annual_historical_unverified.csv`,
`nse_sector_map.csv`, `annual_fundamental_quality_summary.csv`, and the
final-screen summary. Fail-closed: no market/trade signal until independently
verified annual, quarter, sector, and historical market data gates all pass.

## Low-cost, official routes to investigate

1. [NSE Integrated Filing — Financials](https://www.nseindia.com/companies-listing/corporate-integrated-filing)
   provides a **Download (.csv)** option in the browser. This can supply the
   official filing index (symbol, basis, audit label, period and original XBRL
   link) if an API is not available. It is **not** a replacement for verifying
   the actual XBRL facts; only rows with independently validated source XML
   can pass the annual/quarterly source gates. Keep downloaded index snapshots
   as provenance records and never invent missing URLs or filing types.
2. [NSE Financial Results](https://www.nseindia.com/companies-listing/corporate-filings-financial-results)
   is a separate official archive to investigate for earlier fiscal years,
   including FY2022-23 and FY2023-24. The integrated-filing API currently used
   by this project has produced verified results for FY2024-25 and FY2025-26
   only. Old-year history must not be synthesized from newer statements.
   Validate an older document's company symbol, date, audit, consolidation,
   XBRL/financial-statement values and original publication before approval.
3. [NSE Industry Classification](https://www.nseindia.com/static/products-services/industry-classification)
   documents the official four-tier classification. Official
   [Nifty 500 index constituents](https://www.nseindia.com/static/products-services/indices-nifty500-index)
   offer a downloadable CSV for a **subset** of the market. Do not call an
   index-membership list a verified four-tier classification of all NSE
   mainboard companies. If exact fields are missing, sector stays unknown.
4. [NSE Data & Analytics](https://www.nseindia.com/static/nse-data-and-analytics)
   offers formally licensed historical and corporate data as a paid alternative.
   Check terms, permissions, coverage and current tariff before considering it;
   prefer free official browser exports and authorized sources for this
   cost-sensitive personal research project.

## Automated low-cost legacy index attempt

The scheduled workflow now tries the official legacy Financial Results *metadata*
endpoint with two bounded historical publication windows (FY2022-23 and
FY2023-24), using `nse_legacy_index_fetch.py`. The read-only index fetch is
NOT an audited-fact extractor, and it stops if NSE returns HTTP 403/429.
Its result is recorded in `legacy_index_fetch_status.csv`. If authorized
access succeeds, raw metadata is saved as
`nse_legacy_financial_results_2022-23.csv` and
`nse_legacy_financial_results_2023-24.csv`. A later audit creates
`legacy_annual_recovery_manifest.csv` and
`legacy_annual_recovery_summary.csv`.

A March-quarter statement is not interchangeable with a full fiscal-year
annual statement. Even an audited consolidated index row remains
`PENDING_INDEPENDENT_XBRL_REVIEW` until the original official filing and its
consolidation, audit, annual duration and financial facts are verified.
Index candidate files are never copied into `annual_historical_results.csv`.
If the archive API remains unavailable, a separately downloaded official
browser CSV can be reviewed by the same manifest checker. No automation
promotes the source solely on a matching symbol or CSV headline.

## Safe ingestion principles

- Never evade access controls, rotate proxies to bypass denial, use stolen
  credentials, or overload the exchange. Wait for permitted access or obtain
  an authorized feed. HTTP 403/429 is a **source-access failure**, not evidence
  that a company has no filings or zero financial figures.
- Download only via authorized official NSE or company investor-relations
  channels. If using locally supplied documents, record URL, retrieval date,
  SHA-256, symbol, reporting basis, period and audit information.
- Locally supplied files or index CSVs are **research inputs**, not proof of
  official network provenance. Authenticate facts against a trusted primary
  document/source before marking NSE metadata/XBRL verification flags true.
- Do not combine a missing fact from one revision with a different verified
  revision or assume missing borrowings/CapEx/cash flow means zero.
- Financial, REIT/InvIT, utilities and other specialist sectors still need
  appropriate independent metrics beyond the general-company ratios.
- Do not place broker login tokens, TOTP seeds, private daily market data, or
  app secrets in this GitHub repository.

## Definition of done

The NSE collector must produce reliable verified 2022-23 to 2025-26
**audited consolidated** source history for the intended symbols, usable sector
classifications, quarterly comparison coverage, and independently adjusted
market data/backtest evidence. A green workflow and an empty shortlist are
operational diagnostics, not trading readiness.


## Bounded legacy XML verification (16 October 2026)

`python nse_legacy_xbrl_verify.py --limit 100` independently downloads only
unique official HTTPS XML candidates from the existing baseline/index audit.
It runs before integrated-filing collection, so that endpoint cannot prevent
the separate legacy recovery step from being attempted. Duplicate revisions
remain manual-review cases. Never-attempted URLs are prioritized over retries.

Before an annual row is appended, the verifier requires matching XML company
symbol and NSE entity identifiers; exact April 1–March 31 duration contexts;
year-end balance-sheet contexts; explicit annual-context audited labels;
consolidated reporting basis; INR/INR-per-share units; finite numeric values;
and no conflicting duplicates/aliases. The currency unit, not `decimals` or
presentation-rounding labels, determines conversion. The original response
SHA256 and index SHA256 remain attached. Missing financial fields stay blank;
a source-verified incomplete row is still not a full fundamental PASS.

HTTP redirects are refused; 403/429 and network failures stop the batch.
Existing history is preserved on rejection and source failures. Source files
have a 5 MB limit. `legacy_xbrl_verification.csv` records per-URL reasons;
`legacy_xbrl_run_status.csv` distinguishes bounded progress from completed
coverage. A run is not proof that all legacy history has been recovered.

Observed old filings can have annual-looking human-readable labels while the
actual XBRL context covers January–March only. These are rejected rather than
silently rewriting context dates. They need an independently verified corrected
filing or annual financial statement; this verifier does not extract PDFs.
