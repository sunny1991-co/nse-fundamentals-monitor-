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

## Quarterly parser v6 migration

ISO dates are parsed deterministically, without day-first reinterpretation.
Older raw quarterly rows remain stored, with their old/blank `Parser_Version`,
but are excluded from comparisons and final shortlist decisions. The collector
prioritizes their source URLs for verification. The collection status includes
`Stored_Quarter_Rows`, `Verified_Quarter_Rows` (current parser only),
`Reverification_Required_Rows`, and `Parser_Version`. A temporary decrease in
verified counts during migration is expected and must not be hidden.
A current source-status version is necessary but not sufficient: individual
quarterly comparison rows must also carry the current parser version.

## Actual enforcement of old NSE original XBRL source preflight

`nse_legacy_xbrl_preflight.py` now provides an offline source READY/not-READY
classification, including metadata-only Annual/Audited/Consolidated dates,
publication time, different issuer/revision links, earlier 403/429 and
network source cooldowns (20 hours), and XML previously rejected for
full-April-to-March annual contexts (e.g. Q4-only).

**Important safety closure**: printing a preflight report by itself does
not restrict an independent downloader. The supervised Termux maintenance
invocation of `nse_legacy_xbrl_verify.py` now includes
`--require-preflight`. In that mode the verifier **re-runs**
the source preflight from official locally-attested NSE EQ catalog and
source/index records, then intersects its own independently rebuilt
baseline-issuer/year/source URL manifest with exact
`READY_FOR_INDEPENDENT_ORIGINAL_XML_CONTEXT_REVIEW` preflight rows
BEFORE initiating any original XBRL source request.

If NSE's mainboard snapshot is stale, source index is missing its
broadcast-date evidence, previous NSE download was blocked/recent,
or old XML was rejected due to quarter-only context, affected source
URLs are **not re-requested**. If current NSE EQ identity or preflight
itself fails, the scheduled verifier fails closed and does not
download original XBRL or write verified financial history. Existing
backtest/shortlist/trade orders remain unchanged.

Status ledger `legacy_xbrl_run_status.csv` contains
`Original_Index_Candidates_Before_Preflight`,
`Excluded_By_Current_Source_Preflight` and
`Independent_Source_Preflight_Required`, as well as genuine
attempted and independently verified six-core annual results.

Manual inspection without NSE network:

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_legacy_xbrl_preflight.py

Optional small original XML batch, ONLY after checking the ready count
and respecting source access conditions:

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_legacy_xbrl_verify.py --limit 3 --require-preflight

The second command can make up to three archived NSE XML requests
(if allowed by the independently regenerated ready index).
It does not use the old Financial Results *metadata API*.
The original verifier still validates context duration, original
symbol/entity, audited and consolidated flags, currency and complete
six-core facts; source index links alone never count as verified.

## Zero-network four-year original-XBRL ready-source plan

For the 2,515 previously observed *indexed old-year XBRL URL stages*,
the user's actual number of original independently verifiable annual XMLs
is not yet known. Do not conflate Annual/Audited/Consolidated source
metadata with source-verified full April–March XML contexts. The
scheduled downloader's `--require-preflight` gate rejects missing
broadcast-date evidence, shared URLs, already rejected Q4-only XMLs,
previous download failures within 20h, and stale NSE EQ discovery.

New command:

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_legacy_xbrl_verify.py --limit 10 --require-preflight --plan-only

The read-only plan refreshes the same strict local original NSE source
index preflight, ranks candidates, and previews **up to 10 symbol/FY
pairs** with a research priority and an "other old FY verified in local
source history" flag. It creates NO network requests or HTTP session,
does not update the XML attempt ledger or audited historical facts,
and does not alter the strict four-year screen or broker orders.
The reported `Freshly_Preflighted_Ready_Candidates` count comes from
your own current local sources; no success/fail is assumed.

Priorities within the *already source-ready* URLs favour companies
with both newer FY2024–25 and FY2025–26 six-core baseline rows,
especially if another original FY2022–23/FY2023–24 year is already
verified locally. Absent or incomplete newer baselines get lower
research preference; their index links are retained, not deleted.
**Previously never-attempted source URLs are considered before
retrying prior failed URLs.** This is source collection efficiency,
not a stock-quality score, financial pass or investment signal.

`--plan-only` always requires `--require-preflight`. Without
current original NSE EQ/ISIN attestation it fails closed. Use it
first when old source access has been unreliable. Actual subsequent
downloads remain conditional on NSE allowing authorized access
and the original XML independently passing the strict context and
numeric verifier.

## Faster source preflight for >2,500 older NSE index links

A real local Mainboard snapshot has reported 2,515 indexed older
company-year NSE annual XBRL source URLs, but **not** audited verified
XML facts. The first preflight algorithm filtered each issuer/year
by repeatedly scanning both full source index tables. On mobile
Termux this can make the read-only `--plan-only` command spend
unnecessary time on repeated metadata string parsing.

The source metadata preflight now builds one exact
`(Symbol, Original_XML_URL)` index per year **after** requiring
Audited, Consolidated, Annual, exact fiscal-year end and approved
original NSE XML URL. This makes the per-company original metadata
row lookup nearly constant-time instead of searching thousands of
rows for every issuer. It does not change which annual filing XML
context is required, and it still calls no NSE API.

The source-authority check is also stricter: two matching official
index CSV rows for the same issuer/year/source URL are
**ambiguous even when broadcast dates match**. The system leaves
them in manual source-review and will not download the same source
under an automated preflight-ready flag. Prior source cooldown,
historical data and disabled orders are unchanged.

## Distinguishing failed exact-year NSE XML contexts without re-downloading

A real Termux `--limit 3 --require-preflight` run at
`2026-10-17T12:06:57Z` attempted three original NSE HTTPS XML
sources: BOSCHLTD FY2022–23, BOSCHLTD FY2023–24 and BPCL FY2022–23.
All three received `EXACT_APRIL_MARCH_CONTEXT_NOT_VERIFIED`;
`Added_Verified=0` and `Added_Six_Core_Complete=0`.
The source-preflight result before that attempt had 2,652 baseline
index candidates, 587 blocked from independent XML inspection and
2,065 metadata-ready. These are NOT 2,065 XML/audited-fact passes.

`EXACT_APRIL_MARCH_CONTEXT_NOT_VERIFIED` is a composite strict
source-verifier error, **not proof that every rejected XML has only
January–March quarter data**. Even where the original XML contains
an exact April–March period, an unverified NSE issuer identifier scheme,
standalone-specific context or another unsupported dimension can
invalidate its use as consolidated annual audited evidence.

For *future* downloads already permitted by original source preflight,
the strict legacy XML verifier now reuses **the XML bytes it has
already downloaded** to run the source context diagnostic if the
original annual duration or year-end balance-sheet check fails.
It records only safe reason codes and duration/context counts in
`legacy_xbrl_verification.csv`:

- `Context_Diagnostic_Reason`: precise explanation such as
  `EXACT_ANNUAL_DURATION_MISSING`,
  `ANNUAL_DURATION_EXISTS_BUT_CANONICAL_NSE_ENTITY_MISSING`,
  or `ANNUAL_NSE_ENTITY_CONTEXT_HAS_UNVERIFIED_DIMENSIONS`.
- Counts for exact annual period, strict eligible annual duration,
  fiscal-year-end instant and quarter-length duration ending at
  the fiscal-year-end.
- `legacy_xbrl_run_status.csv` summarizes these reason counts
  and distinguishes `Attempted_Unverified_This_Run` from any
  successful original source XBRL verification.

**No second HTTP request is made for these diagnostics.** No
rejected annual XML source is automatically retried; the existing
source preflight skips old context rejections or failed recent access.
The three BOSCHLTD/BPCL XML bodies from the previous attempt were
not retained, so the new diagnostic cannot retroactively classify
those three without an unwanted second source request.

Raw XML text, company/private identifiers, unsupported dimension
values and numeric facts are not copied into the source diagnostic.
The original strict annual source rejection code remains unchanged
and authoritative. Neither PDF candidates nor synthetic annual
columns become a verified source, and broker orders remain disabled.

## Unattended legacy XML zero-yield safety guard

The user's real source-file evidence shows that the 2026-10-17T12:06:57Z
small original NSE XML batch fetched three Annual/Audited/Consolidated
*index* candidates (BOSCHLTD FY2022–23, BOSCHLTD FY2023–24, BPCL
FY2022–23); none passed the strict April–March original XBRL
context gate. A later source-verified GitHub ledger check showed 811
source attempts across October 16–17: 767 strict year context
rejections, 42 XML symbol/issuer identity failures, and 2 HTTP 404;
**zero newly verified old Annual XBRL facts**. The XML context
rejection alone does NOT prove every rejected source is quarterly
only: identity schemes or dimensions may also be responsible.

The repository's prior unattended Termux maintenance used
`nse_legacy_xbrl_verify.py --limit 100 --require-preflight`
every day. Metadata readiness of 2,062 further original NSE URLs
is **not evidence that their actual XML has usable full annual
audited facts**; another bulk batch can needlessly generate source
traffic and retries.

The new bounded operational rule is `--respect-yield-guard` alongside
the **still-mandatory** `--require-preflight`. It examines the last
50 REAL original source XML responses recorded under the current
source parser version, identified by a non-empty 64-character
source-response SHA256 and timestamp; plain CSV index links
do not count. If at least 30 actual XML source responses exist and
90% or more of those have strict source-context/issuer structural
rejections with ZERO verified annual results, the unattended batch
returns `UNATTENDED_ORIGINAL_XML_SOURCE_ZERO_YIELD_PAUSED`
before creating any HTTP session. It emits recent yield counts,
the unresolved number of already-preflighted source URLs and the
recommended action `REVIEW_ALTERNATE_OFFICIAL_AUDITED_ANNUAL_SOURCES`.
This leaves the original XBRL verification ledger, previously
verified annual history, strict company screen, and trading state
unchanged. It DOES NOT declare that all remaining old original
filings are invalid, and does not rewrite a Q4 filing as Annual.

Daily Termux maintenance now caps this older source step at **3
source URLs** even when its yield guard does not activate, instead
of 100. The verified FY2024–25 and FY2025–26 integrated filing
collection, offline PDF/archive research, quarterly comparison,
sector checks, and safety screens remain separate and are NOT
paused by this old-year source-yield guard.

View current original XML metadata candidates and latest source yield,
without any NSE HTTP access:

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_legacy_xbrl_verify.py --limit 10 --require-preflight --plan-only

The scheduled guarded check may be run explicitly, but **do not**
manually download or repeatedly retry source XML just to bypass
an observed near-100% structural rejection rate. A different,
independently authenticated original full-year audited source and
associated source/version verification is needed before original
four-year audit screening can progress. No paid AI or broker
orders are involved.

## Recover existing official Annual Report PDF links without repeating failed metadata calls

The older NSE Financial Results XBRL XML verification currently suffers
a persistent structural source zero-yield pause. The **separate official
Annual Reports PDF archive route** was already available but its
`nse_official_annual_pdf_auto_index.py` collector normally opens a
fresh `www.nseindia.com/api/annual-reports` metadata session before
archiving any report. That can prevent using a previously validated
official archive PDF link when the metadata endpoint is blocked or slow.

New **cached-index-only** option:

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_official_annual_pdf_auto_index.py --cached-index-only --download 0

This performs a fresh OFFLINE NSE mainboard EQ/ISIN validation using
the project's attested local NSE discovery, rechecks every previously
stored PDF metadata row against original report URL host/path, ticker,
fiscal year, metadata checksum format, issuer's current official ISIN
and versioned PDF candidate source label. Existing source receipt paths
and indexed original PDF source SHA references are crosschecked too.
It reports the number of original NSE PDF links already archived,
not-yet-archived links, and the next up to eight research-source
symbols/years **without creating an HTTP session or modifying the
original metadata/source receipt ledger**.

If this proves there is at least one *previously indexed and unarchived*
annual report, a tightly controlled source-document fetch can use:

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_official_annual_pdf_auto_index.py --cached-index-only --download 1

That command can make **at most one HTTPS request to the exact original
NSE archive PDF link**. It does not call the Annual Reports metadata
endpoint or the old Financial Results API, does not refresh metadata
and cannot bypass a 403/429 returned by the actual PDF archive.
Each source document is subject to the existing 26 MB per-PDF budget,
1 GB total local PDF budget, local free-space minimum, PDF headers/EOF
format, private path, original SHA256 receipt, and full subsequent
consolidated PDF source page/number review gates. Neither indexed
metadata nor the PDF itself becomes strict independently verified
XBRL annual facts or a stock recommendation.

The separate private status
`runtime/nse_official_annual_pdf_cached_intake_status.json`
ensures inspecting/archiving already indexed PDFs **never resets the
original NSE annual metadata API's 20-hour source-access cooldown**
in `runtime/nse_official_annual_pdf_collector_status.json`.
The source document download limit now caps **total HTTPS attempts,
including failed 404s**, rather than only successful PDFs. This
applies to the pre-existing PDF collector too.

If `validated_indexed_pdf_links_pending_archive=0`, no source PDF
is requested. This is a source-intake fallback, **not** a method to
invent old full-year fundamentals, skip original NSE audit statements,
or claim more complete historical coverage. Trading remains DISABLED.

## PDF-first official NSE old-year source recovery after confirmed XML zero yield

The actual 2026-10-17 Termux dry-run confirmed 2,652 old original-XBRL
INDEX candidates, 590 excluded by strict index source preflight and
2,062 source-index-metadata-ready items. Across the last 50 *real XML
responses* in the source verification ledger, **50 were rejected and 0
were verified**. This is a source format and provenance blocker,
NOT evidence that any company's revenue/profit/borrowings are zero,
nor that all 2,062 remaining original XMLs are invalid.

The separate official NSE Annual Report archive offers a plausible
alternative original document source for 2022–23 and 2023–24.
A 2023–24 report often also contains **comparative FY2022–23**
consolidated figures; however, any such older-year column remains a
**later-document PDF research candidate, not independently verified
old-year original NSE XBRL facts or a strict annual PASS**.

`nse_legacy_pdf_first_recovery_queue.py` is a fully **offline**
actionable plan for all current, attested NSE mainboard EQ issuers,
joined by exact symbol and ISIN to the independently recomputed
two-FY evidence queue. It prioritizes baseline-tracked symbols with
two six-core newer-FY rows, then source work for already indexed
or saved original PDFs. Its individual actions distinguish:
- originally indexed 2023–24 official NSE Annual Report PDF requiring
  a bounded archived PDF retrieval
- cached original FY2023–24 NSE PDF requiring consolidated statement
  source-page/numeric review
- existing FY2023–24 PDF current-year figures requiring cautious
  FY2022–23 comparative extraction from original same report
- both FY2022–23 and FY2023–24 PDF candidates obtained for *research
  only* but separate original audited sources still needed for strict
  multi-year fundamental review
- missing indexed PDF source after a prior annual metadata lookup,
  requiring an alternative independently authenticated official
  annual-report archive rather than pretending no annual report exists
- original annual report metadata lookups for issuers not yet
  source-indexed, conditional on official NSE API access and cooldown

Outputs are
`nse_legacy_pdf_first_recovery_queue.csv` and
`runtime/nse_legacy_pdf_first_recovery_status.json`.
They contain only current source eligibility and *what to research
next*, not predicted stock price, a recommendation, audited-fact
promotion, or a broker action. Existing old XML verification yield
guard, 20-hour original API denial cooldown, independent issuer+ISIN
checks and strict trading gates are preserved. Daily Termux
maintenance runs this new read-only plan after original NSE source
gap inventory. No HTTP sessions or API calls are made.

The existing `nse_official_annual_pdf_auto_index.py` also now
prefers baseline-tracked companies when prior lookup time and
six-core newer-FY coverage are otherwise tied (previously this
comparison inadvertently preferred an untracked issuer).

Manual no-network check:

    cd ~/nse-fundamentals-monitor-
    git pull --ff-only
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_legacy_pdf_first_recovery_queue.py

The actual local mainboard PDF-index and prior-year comparative counts
may be small. Report these honestly; **do not infer they have all been
archived or source-audited**. The PDF plan does not initiate a large
download or bypass older original XML access blocks.

## Safe next step: first six already-indexed original NSE annual PDFs

The Termux source queue on 2026-10-17T14:01:50Z confirmed
2,344 current official mainboard EQ issuers and 4,688 FY22–23/FY23–24
issuer-years. There are **6 companies with existing original
FY2023–24 NSE annual PDF archive links, awaiting archive receipts**,
one 20MICRONS two-year *PDF candidate* source comparison, one issuer
requiring other official archive evidence, and 2,336 issuer records
without an indexed old original NSE annual PDF yet.
Of the same current mainboard snapshot, 1,227 have both newer FY
research baseline rows with six numeric fields; this does **not**
imply those 1,227 passed independent four-year XBRL scrutiny.

The existing original annual PDF cached-index mode is the preferred
next bounded source stage. It reads only the previously indexed
original NSE Annual Report PDF metadata, requires current attested
NSE EQ symbol/ISIN and original `nsearchives.nseindia.com`
archive URL, respects filesystem/storage limits and records PDF
candidate receipts. It does not query the NSE Annual Report
metadata API, the old NSE Financial Results API, or alter broker
orders / strict fundamentals.

The CLI `--cached-index-only` now **defaults to NO PDF downloads**.
Call explicitly with `--download 0` for clarity:

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_official_annual_pdf_auto_index.py --cached-index-only --download 0

This reports exact pending PDF link/receipt counts and the next
issuer/FY source pairs, with zero HTTP sessions. When the source
allows it, a separately intentional *single source PDF request* is:

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_official_annual_pdf_auto_index.py --cached-index-only --download 1

Even a successful archived PDF does not verify financial facts.
The original PDF physical statement sections, source evidence rows,
comparable source labels and annual numbers require further
independent review before *research-only* inclusion. The already
implemented same-PDF comparative amounts are never silently
promoted to original older-year audited XBRL.

Among equal research-priority links, **the FY2023–24 report is
archived before FY2022–23**, because one FY2023–24 document can also
provide the printed FY2022–23 comparative column for research.
The annual metadata API's original 20-hour 403/429 cooldown
timestamp is retained under its own status; any cached-index source
validation error or archive 403 is reported in the SEPARATE
cached-index status instead and **cannot reset the original
NSE API cooldown**. Tests cover these cases.

## Archived original NSE Annual Report PDFs: safe cached-link-first daily intake

Actual 2026-10-17 private Termux source plan: 2,344 NSE EQ Mainboard
companies, 1,597 current baseline-tracked, 1,227 with six core fields
populated in both newer-FY research baselines; 6 old FY2023–24
official original annual PDF index links still need a cached original
document. These links are *not yet downloaded/parsed/verified*.
FY2023–24 PDF current column and FY2022–23 prior comparative values
remain research candidates even after successful source PDF parsing.
The previously attempted old original annual XBRL sources had zero
verified strict annual context yield in the last 50 downloads.

A new source-failure safety guard now applies to
`nse_official_annual_pdf_auto_index.py --cached-index-only`.
The cached index path validates current official EQ Mainboard symbol
and ISIN, metadata source URL and SHA receipt, keeps its own
`runtime/nse_official_annual_pdf_cached_download_attempts.csv`
containing one recent attempt per indexed original PDF URL, and
never calls NSE's annual-report metadata API. **404 gets a 7-day
per-document retry deferment**. A genuine PDF-archive 403/429
activates a **20-hour archive-host-wide cooldown**; other PDF
network/response failures are deferred per URL for 20 hours. All
are reported in the offline status alongside total previously
indexed PDFs, already cached receipts, eligible and deferred URLs.
Mismatched/ambiguous/source-forged old attempt ledger rows fail
closed before any PDF request. The metadata API's independent
old 403/429 cooldown status is never reset or rewritten.

The Termux bounded daily maintenance now separates the two jobs:
(1) official Annual Report *metadata index* discovery,
`--limit 10 --download 0`, and
(2) original **previously indexed PDF** archive,
`--cached-index-only --download 1`. A source index API denial
does not cause the cached-only stage to hit that blocked API,
and cached download attempts cannot exceed **one** per run.
Even a PDF 404 or 403 consumes the attempt budget. Successful
PDF archive receipts still require original SHA and independently
located audited consolidated statement pages, accounting checks
and source audit; nothing enters historical XBRL facts or strict
screens automatically.

To inspect the real six existing indexed PDF candidate links with
ZERO NSE HTTP requests:

    cd ~/nse-fundamentals-monitor-
    git pull --ff-only
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_official_annual_pdf_auto_index.py --cached-index-only --download 0

Only when its report says at least one original PDF is eligible,
the user can explicitly run a **one-file** bounded archive:

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_official_annual_pdf_auto_index.py --cached-index-only --download 1

A failed result stays in the dedicated source attempt cooldown;
the same stale/blocked PDF URL is not immediately retried.
An archived PDF adds NO audited XBRL-verified financial fact,
stock signal, SMS alert or live broker trade, and orders remain
DISABLED.
