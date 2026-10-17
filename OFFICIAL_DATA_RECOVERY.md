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
