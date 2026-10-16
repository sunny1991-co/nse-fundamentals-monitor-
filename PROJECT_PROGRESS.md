# Project progress — 16 October 2026

## New explainable equity-swing assistant and owner YES/NO+quantity paper workflow — 16 October 2026

Owner's requested future UX: software automatically chooses strong
fundamental+technical stock, waits for valid entry band, asks the owner
only **YES/NO and quantity** before placing any new trade, then manages
stop/target/trailing/exit without further routine decisions. We built
the complete **research and PAPER rehearsal** portion of this pattern on
GitHub with an intentionally hard boundary against live orders.

### What is implemented

- **Quality Leader Contraction Breakout v1** frozen deterministic technical
  hypothesis: strict NSE audited consolidated current fundamental
  shortlist, exact-date official NIFTY 50 TRI rising regime, 63-session
  stock-vs-TRI leadership, rising EMA20/50/200, previous 30-session
  breakout, volume >=1.2× prior mean, volatility contraction, RSI
  54–74, ATR 1.2–7%, prior daily turnover >=₹5 crore. Only
  **long NSE EQ** and **next-session** no-chase entries. Causal
  research features; no future-data information in signal.
- Entry proposal includes initial stop 2–10%, 2R and 3R targets,
  maximum 100 NSE completed sessions (approximately up to 5 months),
  <=1% *modeled* account stop risk and <=25% position concentration.
  These are not guaranteed gap-loss limits.
- New cost-inclusive frozen retrospective research runner reuses
  existing estimated Groww DELIVERY/CNC charges (incl DP, taxes),
  next-open/stop-first model, and doubled fee+slippage stress.
  Previously inspected sample does **not** establish independent
  profitability. Real validation still zero.
- Strict daily watchlist only from completed adjustment-attested
  OHLCV, independently submitted source-attested official NIFTY 50 TRI
  and **current strict NSE audited fundamental pass**. Vendor Upstox
  data cannot substitute for official financial records; missing
  benchmark or screened records yields **no trading proposals**.
- Read-only market worker matching verified NSE_EQ quotes at entry
  range automatically creates unique, SHA-bound **PAPER** proposals.
  Neither a user typing a symbol into the web UI nor an unverified
  price can create an actionable live trade.
- Owner-facing MFA/CSRF `/assistant` interface asks only
  **YES/NO plus explicit whole-share quantity**; defaults are never
  silently supplied. Approval expires after 5 minutes, cannot be
  replayed, and approval counts **zero** real orders.
- Approved paper positions progress through simulated entry,
  gap-aware stop-first, partial 2R target, final 3R target,
  completed-candle 2×ATR trailing and 100-session time exit.
  EOD process insists on all intervening verified calendar sessions
  and adjusted NSE OHLCV; it never pretends the stop filled during
  network downtime or replays highs/lows before original owner YES.
- Integrated in existing supervised Termux maintenance, quote
  worker and status dashboard, without using trading-capable tokens
  or any broker order endpoint.
- Hard-coded research capital ₹1,00,000 is **only a paper size
  illustration**, not Groww account cash or a personalized position.
  Quantity entered by user may only be <= paper risk-capped maximum.
- Synthetic regression tests cover causal data/benchmark,
  quote/metadata provenance changes, source gaps, stale quotes,
  replay and invalid quantity, MFA+CSRF, EOD stop-first and
  100-session time exit. Recent GitHub Actions passed for committed
  code; unit tests are NOT real-NSE strategy profitability evidence.

### Live Groww remains disabled

Official Groww Trading API can support order placement, status, trade
fills, and smart orders, but account entitlement must be verified.
Official documentation supports **CASH+CNC GTT**. It explicitly
describes **CASH OCO as MIS**, so an OCO suitable for a 1–5-month
**CNC delivery** equity holding is **not proven**. We will not
place the real BUY until broker-side stop/target protection for
CNC, partial fills, exchange order statuses, account balance,
kill switch, failures/restarts and support for modifying stops
are proven in a test/live-permissioned environment:
https://groww.in/trade-api/docs/curl/orders
https://groww.in/trade-api/docs/curl/smart-orders

Other real-money blockers remain: **zero validated profitable
strategies**, zero current strict fundamental passes, no independent
forward paper track record, no confirmed brokerage execution
permissions, no actively validated SMS/push approval delivery, and
undocumented phone/Android host availability. None were waived.

### Files and activation

See `SWING_AI_APPROVAL_SYSTEM.md` for all code, limitations and
acceptance criteria. Installed mobile UI is
`http://localhost:8787/assistant` after MFA login.

```bash
cd ~/nse-fundamentals-monitor- && git pull && bash termux_backend.sh restart
```

This is the only code activation step required on the phone.
It **does not** enable a live order or bypass missing fundamentals.
Daily scan remains empty until strict source-qualified companies
exist. Approval always denotes a hypothetical paper position.

## Fixed GitHub XBRL false-exclusion diagnostic for SEBI/BSE issuer schemes — 16 Oct 2026

The owner requested **fix directly on GitHub without further Termux commands**.
The previous raw XBRL diagnostic was correctly conservative for trading
but misleading for source QA: it called independently retrieved original
NSE archive values `ARCHIVED_NSE_VALUE_IN_EXCLUDED_CONTEXT_REVIEW`
simply because XBRL's valid `www.sebi.gov.in` or `www.bseindia.com`
issuer namespace differs from the expected NSESymbol scheme.

Implemented in `nse_xbrl_discrepancy_audit.py`:
- **Two completely separate evidence channels:** numerical reproduction
  of an original NSE-hosted XBRL tag and **independent legal issuer
  attribution**. A SEBI/BSE-scheme context may still be used for a
  read-only raw source numeric cross-check, *without* declaring its
  entity/period/basis independently certified.
- New diagnostic verdict
  `OFFICIAL_NSE_XML_FACT_REPRODUCED_ISSUER_LINK_PENDING` when the
  NSE archived crore value matches an **exact April–March INR fact**
  in an archived-selected context, with original NSE source URL,
  matching XML-level symbol, consolidated global claim, and
  **a single, consistent issuer identifier across ALL document
  contexts**. Other company schemes or conflicting issuer IDs are
  NOT automatically accepted.
- Explicitly fail closed on alternate accounting tags with
  **conflicting numeric values**, non-INR units, wrong-year facts,
  extra segment dimensions, standalone document, incorrect context
  references, or mixed legal-entity context identifiers.
- Keep historical FY CSVs, issuer metadata, strict fundamental passes,
  sector classification, Upstox vendor-reference records, order
  permissions, backtests, and paper trading **unchanged**.
  A reproducible raw fact is still **NOT a trading/accuracy approval**.
- Separate counts
  `official_nse_xml_numeric_reproductions_pending_issuer` and
  `issuer_identity_attestation_pending_metric_cells`; expose
  via `project_status.py` with another strict false attestation flag.
- Added synthetic unit tests including an end-to-end local-offline
  original NSE file drilldown showing positive numeric reproduction
  and zero audited issuer attestation / trades.

Latest owner-reported real NSE archive audit (prior to this patch):
four XML downloads, 5 exact-period NSE archived-value raw matches
whose issuer schemes were 3 SEBI and 1 BSE documents, all with
single internally consistent entity identifiers; **no issuer's
cross-exchange scheme was independently matched to an official
symbol→legal entity ID mapping**. The five *new* numeric-reproduction
verdicts are a predicted outcome of these source characteristics, but
have **not** been recomputed on the user's Termux after the GitHub
patch. GitHub Actions regression tests are synthetic and do not
substitute for owner-local re-execution.

Therefore: **the false exclusion logic has been corrected in GitHub,
but the actual 104 Upstox-vs-NSE mismatches in 62 companies have not
been resolved by accounting/source definition review.** Independent
issuer crosswalk and consolidated legal-entity attestation are still
a blocker before strict 4-year fundamentals or broker orders.

## Source-original issuer URI schemes identified; cautious XBRL standard handling — 16 Oct 2026

User's actual on-device NSE XBRL output finally identified *which*
identifier-scheme domains exist in the archived XBRL contexts:

- **MWL FY2025-26:** `www.sebi.gov.in`, 18 contexts;
- **TBOTEK FY2025-26:** `www.sebi.gov.in`, 53 contexts;
- **NECLIFE FY2025-26:** `www.sebi.gov.in`, 18 contexts;
- **KRIDHANINF FY2024-25:** `www.bseindia.com`, 12 contexts.

In each of five audited metric cells, the archived NSE number corresponds
to exact-year `INR` XBRL tag in original `FourD` context, but the
**previous diagnostic intentionally required `NSESymbol` scheme** and
therefore did not independently attest the fact's issuer identity.
No unexpected other dimensions. One should **not call these
SEBI/BSE schemes invalid XBRL**. XBRL International's 2.1 standard,
section 4.7.3.1, explicitly permits an arbitrary identifying authority's
scheme URI and makes no guarantee that an application can resolve it:
https://www.xbrl.org/specification/xbrl-recommendation-2003-12-31.pdf
NSE's public XBRL taxonomy guidance also notes interchange/shared
compliance data structures across exchanges:
https://www.nseindia.com/static/companies-listing/xbrl-information

Enhanced `nse_xbrl_discrepancy_audit.py` to avoid the false
**"missing tag"** inference and report safer, separate evidence:
- Classification as **SEBI / BSE scheme**, without declaring either
  independently verified NSE identity.
- Non-sensitive *value-shape only* of the self-declared context
  identifier (CIN-shaped, ISIN-shaped, numeric code-shaped,
  symbol-like, PAN-shaped, other) with **no raw entity identifier
  values** or provider secrets printed in logs.
- Distinct entity identifier pair count, whether all the filing's
  contexts use the same self-declared issuer, and whether they
  internally match the filing's claimed symbol. These are
  *internal consistency* tests, NOT an external company registry
  or NSE-issued symbol↔CIN verification.
- A contradictory filing with **multiple entity identifiers**
  cannot get `RAW_XBRL_FACT_AGREES_WITH_ARCHIVED_NSE_VALUE`; reports
  `DOCUMENT_CONTEXT_ENTITY_IDENTIFIERS_CONFLICT` instead.
- `--summary` prints only four short company/year source blocks
  (scheme class, anonymized value shape, consistency, metric verdict).
  Detailed source review remains private in `nse_xbrl_drilldown.json`.
- Regression tests using synthetic NSE / SEBI / BSE context schemes,
  anonymous CIN-like identifier, conflicting issuer claims, source
  period/unit/context and no strict trading permissions.

**No** earlier `NSE_Consolidation_Verified` column reclassified,
vendor figures promoted, audited annual original files changed,
strict fundamental companies passed or broker orders allowed.
The original source mismatch **104 cells in 62 companies** remains
under review, 180/1270 vendor references are collected as per
the latest user-confirmed local run.

Next owner-local command:

```bash
cd ~/nse-fundamentals-monitor- && git pull &&
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_xbrl_discrepancy_audit.py --max-files 4 --summary
```

Before allowing vendor/NSE source values into any strict
candidate criteria, corroborate **context identifier value** against an
independent official symbol↔CIN/ISIN filing and reporting basis;
internal repetition or shape is insufficient.

## Five original XBRL facts traced to archived `FourD` context — 16 October 2026

The owner's new context-aware recheck fetched all four original NSE
XBRL documents successfully and showed that **all five** disputed
financial metric/year cells came from the same context ID `FourD`
in each XML, with amounts identical to the original NSE history CSV:

- MWL FY2025-26 OCF: `-20.426cr`
- KRIDHANINF FY2024-25 OCF `-11.0165cr`, Revenue `2.5767cr`
- TBOTEK FY2025-26 OCF `46.44cr`
- NECLIFE FY2025-26 Revenue `0.0cr`

**Zero** additional non-basis XBRL dimensions and **zero** apparent
unit errors were implicated. All five selected `FourD` fact contexts
were correctly rejected by the stricter audit because their
`<identifier scheme="...">` was **not an attested NSESymbol-scheme
identifier**. Context `basis` was **UNSPECIFIED** for all five.
Although original NSE archive URLs, XML symbol and global consolidated
reporting claims match, these DO NOT independently establish issuer
identity for a raw fact's reporting context or audited legal entity.

Root issue: historical `nse_annual_history.parse_annual()` selected
period/dimension-aligned contexts without checking the *context entity
identifier scheme and value*. The archived fact selection is
**reproducible**, but the original parser's issuer-attribution check
was not sufficient for stronger source attestation. Do NOT automatically
rewrite NSE history, promote these values into strict fundamentals or
interpret the differences as vendor errors.

Added forensic improvements:
- Exact classification of context identifier URI scheme (NSESymbol
  vs recognized MCA CIN vs other scheme), without printing company
  identifier values or credentials.
- Optional **document-internal** company CIN-fact agreement checks:
  if context scheme is MCA CIN and the filing's separate CIN fact
  matches, mark as **unverified document-self-claim only**; independent
  NSE/MCA symbol-to-CIN evidence is still required.
- Per-metric archived XBRL context-ID matching (no mixing instant
  balance-sheet and FY-duration contexts), with issuer-identity
  pending counts visible in `project_status.py`.
- Regression tests for forged issuer scheme, mismatched self-declared
  CIN, absent CIN, non-NSE context identifiers and per-metric context
  isolation. Never modifies company values, source rows, strict gates,
  strategy approvals or orders.

Next user-local command (same four official filings, at most 4 HTTPS
GETs, stops on archive 403/429 or other source limits):

```bash
cd ~/nse-fundamentals-monitor- && git pull &&
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_xbrl_discrepancy_audit.py --max-files 4
```

Inspect `xml_context_identifier_scheme_classes`,
`xml_claimed_cin_fact_count`, and each
`excluded_fact_examples_not_verified[].document_cin_relation` before
deciding if an external official symbol↔issuer-ID crosswalk can be
obtained safely. The official NSE source review is still PENDING.
Do NOT treat the legacy parser's `VERIFIED` column as a standalone
guarantee of this *new stricter context-issuer attribution test*.

## Four live original-NSE XBRL downloads exposed context-quality blockers — 16 October 2026

User's on-device raw XML drilldown was **successful on all four official
NSE archive URLs** (no 403, 429, network error) and reported:

- **MWL FY2025–26 OCF:** exact raw XBRL fact of **−20.426cr** reproduces
  the archived NSE value, but its context has **no explicit consolidated
  basis**. Upstox vendor reads +24.25cr; this is NOT a provider
  error determination.
- **NECLIFE FY2025–26 revenue:** exact raw XBRL fact of **0cr**
  reproduces the archived NSE zero, but its context has **no explicit
  consolidated basis**. Vendor reads +9.29cr; zero revenue must NOT
  be considered validated consolidated financial fact without reviewing
  source context, relevant taxonomy and issuer report.
- **KRIDHANINF FY2024–25:** Revenue and OCF facts existed in an
  exact-period fact candidate, but were excluded by **the combined
  non-basis-dimension OR context identifier filter**. The initial
  diagnostic did not distinguish these two causes; no accounting
  provenance conclusion was possible.
- **TBOTEK FY2025–26:** OCF fact similarly excluded by the combined
  context dimension/identifier guard. Cause must be distinguished
  before labeling original NSE extraction an error.

Total of five metric drilldowns: 2 ambiguous context basis, 3 had
no eligible exact-period INR fact under the initial stricter guard.
A previous verified annual parser may use less demanding identifier
checks and select different contexts; this possibility must be measured
and audited before changing the **read-only historical records**.
No original NSE annual CSV values, no Upstox vendor snapshots, no
trading shortlist or orders changed.

Improved `nse_xbrl_discrepancy_audit.py` now adds:
- Compare original archive's **Annual_Context_IDs/Instant_Context_IDs**
  against raw XBRL fact `contextRef`; surface the **archived NSE number
  found in an excluded context**, rather than confusing that with an
  entirely missing XBRL fact;
- Differentiate other XBRL dimensions, NSESymbol identifier mismatches,
  unverified company identifier schemes (such as CIN), standalone/
  conflicting context basis and unverified monetary units; report a
  small capped sample of candidate tag, context ID and dimension name,
  without assuming a context is consolidated;
- Require real resolved QName namespace URI for `iso4217:INR`, reject
  forged `foo:INR` or ratio units. Refuse documents with ambiguous
  namespace redefinitions;
- Keep a hard cap of four official XML downloads, stop on source HTTP
  403/429, 5xx and network issues; fail closed on unclear monetary facts.
  Synthetic regression tests cover dimension/archived-context match,
  forged unit QName, CIN identifier, conflicting aliases and same-period
  fact selection.
- Expose raw XBRL audit verdict counts separately in `project_status.py`,
  explicitly **not** altering audited NSE verification, historical
  quality score, eligible stock picks or Groww execution.

No claim that the three excluded facts were wrong or that the two
reproduced raw values represent trustworthy final consolidated numbers.
Next user run:

```bash
cd ~/nse-fundamentals-monitor- && git pull &&
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_xbrl_discrepancy_audit.py --max-files 4
```

The original report is refreshed in
`runtime/experiments/upstox-nse-historical-reference-v1/
upstox-reference-batches-v1/reconciliation/nse_xbrl_drilldown.json`.

## 180-company live vendor-reference success and raw NSE XBRL diagnostic — 16 October 2026

Owner confirmed that manual four serial 25-stock catch-up batches finished
normally, making **180/1270** company vendor-reference records completed;
**1090** mapped companies remain. The manual session fetched **400/400
requests** and 100/100 new records, without altering the automated 25/day
limit or any order/strict-screen eligibility.

The subsequent owner-local offline audit found **1,420 source comparison
slots**, including **1,258 within source tolerance** and **104 differing
numeric metric cells across 62 companies**, **27 missing vendor metrics**,
**31 missing original NSE numeric fields** and **20 slots without an
independently verified original NSE year**. Of paired nonmissing numeric
facts, 1,258/(1,258+104)=**92.4% tolerance agreement**, a source
agreement measure, NOT predictive accuracy or trading strategy validation.

The real 104 discrepancies were: **64 revenue, 28 operating cash flow,
8 net profit, 4 assets**. Financial-year split: 64 FY2024-25 and 40
FY2025-26. The local forensic audit found **103 unexplained source gaps**
and **ONE possible adjacent-fiscal-year match**; these are hypotheses,
not confirmed causes or data corrections. Extreme examples:
- MWL FY2025-26 OCF: Upstox +24.25cr vs archive NSE -20.426cr
- KRIDHANINF FY2024-25 OCF: Upstox +5.28cr vs archive NSE -11.0165cr
- TBOTEK FY2025-26 OCF: Upstox -20.77cr vs archive NSE +46.44cr
- NECLIFE FY2025-26 revenue: Upstox 9.29cr vs archive NSE 0.0cr
- NECLIFE FY2024-25 revenue: Upstox 1.88cr vs archive NSE 1673.0743cr
- LUPIN FY2024-25 OCF: Upstox 2999.94cr vs archive NSE 429.94cr
These **are not verified provider errors**; original filings, XBRL tags
and contexts are required before interpreting differences.

To investigate potential extractor/statement-basis/data-lineage errors,
added `nse_xbrl_discrepancy_audit.py` with tests. It takes the owner-local
source-validated forensic CSV and independently source-verified archived NSE
annual XBRL URLs, attempts **at most 4 original official NSE XML downloads
per invocation**, rejects redirections/DOCTYPE/excessive data, verifies
exact currency unit INR, company symbol, XML consolidated nature, context
dimensions, exact April 1–March 31 duration/instant and *all* candidate
XBRL accounting aliases without arbitrarily selecting the first conflicting
fact. It tests whether the prior NSE-extracted crore value can be reproduced
from exact-year eligible XML facts, and raises a review flag on missing tags,
non-INR units, ambiguous numeric facts or non-verifiable context basis.
**Crucially it does not overwrite any original NSE facts and does not
certify the underlying audit or the vendor's statements.** A 403/429 or
network/5xx error safely halts the bounded archival attempt. Outputs
a private `runtime/experiments/upstox-nse-historical-reference-v1/
upstox-reference-batches-v1/reconciliation/nse_xbrl_drilldown.json`.
Historical missing NSE XML must remain a blocker, not be guessed or
promoted from Upstox.

Next user-local raw original NSE audit (first 4 high-priority
filing URLs) is only one optional safe command:

```bash
cd ~/nse-fundamentals-monitor- && git pull &&
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_xbrl_discrepancy_audit.py --max-files 4
```

Do not run additional large manual Upstox catch-up without considering
Upstox's applicable rate limits and original-source data quality.
**Zero independently certified additional NSE historical years**, zero
official NSE sector classifications, zero strict fundamental passes and
zero validated profitable strategies from vendor reference ingestion;
Groww orders remain DISABLED.

## Accuracy-first forensic reconciliation + optional bounded catch-up — 16 October 2026

User's latest real local output:
- 80 Upstox NSE_EQ vendor reference companies checked.
- 624 original-source/financial-year metric slots: 561 within
  tolerance, 36 source discrepancies across 25 issuers, 11 missing
  vendor values and 16 missing NSE original values; another 16 slots
  had no independently source-verified NSE fiscal year.
- Disagreements by metrics: **23 revenue, 9 operating cash flow, 4 net
  profit, and 0 total assets**.
- High-priority review labels, **not bad-stock or trading rankings**:
  KRIDHANINF (147.928%, possible sign/zero conflict), NECLIFE (100%,
  sign/zero conflict), LUPIN (85.668%), 63MOONS (80.168%).
  No finding is a proven source error until the actual XBRL tags,
  statement units/definitions and vendor methodology are inspected.
- Original FY2024–25 and FY2025–26 files, provider token, official
  sector classifications and all trading decisions remain unchanged.

Built `upstox_reference_forensics.py` for an **offline per-cell forensic
comparison** of the prior source-hashed, original-NSE-verified reconciliation.
It rechecks both source file hashes, prior comparison SHA256, matching ISIN
and saved Upstox values, exact original NSE XBRL URL and published date, and
exact numeric arithmetic. For each discrepancy it records NSE/Upstox crore
values, year, sector label (reference only), metric, gap, original NSE
filing URL and unverified possible explanations. Only **hypotheses**:
revenue versus original NSE total income, net profit versus original NSE
PBT, adjacent financial year, sign inversion, or factor-of-10/100; otherwise
flags **UNEXPLAINED_REQUIRES_ORIGINAL_FILING_REVIEW**. No inferred
explanation can adjust NSE XBRL, vendor facts, source verification or
trade status. Outputs are private at `runtime/experiments/
upstox-nse-historical-reference-v1/upstox-reference-batches-v1/
reconciliation/source_discrepancy_forensics.csv`.
The same offline triage now runs after each successful daily
reconciliation and source review queue, but has no access to order APIs.

Added optional `upstox_reference_catchup.py` for **owner-invoked,
bounded, read-only catch-up**: at most **100 new company attempts**, in
four serial independent batches of at most 25 with a 10-second rest between
batches, using the existing checkpoint and per-batch exclusive lock.
A 401/403/429, 5xx, network failure, bad mapping, no-success batch,
or contradictory trading status **stops the session**. This manual feature
**does not increase the existing automated 25/day hard cap**.
Upstox's documented standard API rate limits are 50/sec, 500/min and
2000/30m (see official developer rate-limiting documentation); do not
assume the Fundamentals category has an independently guaranteed quota,
and treat actual 429 responses as authoritative. The manual option is
to help finish existing 1,270 current NSE_EQ company references faster
*without* automated aggressive concurrency or changing trading rules.

Owner's next Termux commands (single line, offline audit first then up
to 100 additional companies only if choosing fast manual catch-up):

```bash
cd ~/nse-fundamentals-monitor- && git pull && bash termux_backend.sh restart && NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_reconcile.py && NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_review.py && NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_forensics.py --top 10
```

Optional **separate** manually initiated data catch-up:
```bash
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_catchup.py --companies 100
```

The mandatory next truth gate is **original audited consolidated NSE
financial statements for fiscal years 2022–23 and 2023–24**, official NSE
sector classifications, complete company-specific accounting definitions,
corporate action data QA and profitable cost-inclusive forward out-of-sample
strategy validation. None have been silently waived. Read-only research
and paper-trading framework continue. Live orders remain DISABLED.

## Measured Upstox/NSE source comparisons and prioritized review queue — 16 October 2026

The owner successfully ran the offline `upstox_reference_reconcile.py` on
the locally collected **80 Upstox vendor-reference companies**. Actual
local output (not synthetic results):
- **624 metric-by-year comparisons** to verified NSE history with a
  matching company/year source; **561 within 5%/₹0.1-crore tolerance**,
  **36 discrepancies** flagged across **25 companies**, **11 Upstox
  metrics missing**, **16 missing NSE metrics**.
- Another **16 metric slots** lacked an independently verified NSE
  financial year, so they are NOT counted as comparisons.
- **597** numerical paired metrics = 561 + 36; tolerance concordance
  is **561/597 ≈ 94.0%**. This is an exploratory *source agreement* measure,
  not a 94% accuracy/stock-win forecast.
- **79** vendors had at least one comparable NSE metric. The displayed
  **2,949 verified original symbol-years** counts the entire local NSE
  historical corpus, not just the 80 vendor issuers.
- **Zero NSE-verified annual XBRL records or official NSE sector
  classifications added; zero strict fundamentals passes**, all
  broker order placement remains disabled. In particular, an Upstox
  sector label must not satisfy NSE sector verification.

Added `upstox_reference_review.py` to turn the original 36 discrepancy
cells into a private, deterministic **company review queue**.
It groups by stock symbol, FY2024–25 / FY2025–26 year, revenue/profit/assets/
OCF, magnitude and sign conflicts. Source-review urgency tiers (NOT stock
rankings) include sign/zero conflict, ≥20%, ≥10%, and over original tolerance;
financial specialists like banks require separate accounting-definition
review. Prints only **top 10** plus totals and saves full prioritized
company table privately under
`runtime/experiments/upstox-nse-historical-reference-v1/
upstox-reference-batches-v1/reconciliation/company_source_review_queue.csv`.
Both raw NSE/source file digests and vendor-mapping digest are compared to
the original reconciliation report; the full metric-comparisons CSV is
also SHA256-bound in the report. Changed NSE rows, vendor ISIN, saved vendor
financial facts, duplicate comparison cells, source row counts or CSV digest
**fail closed**. No business interpretation or data correction is automatic.

Future supervised nightly Upstox batches will automatically refresh the
read-only review queue after the local NSE-vendor reconciliation, recording
review status separately from provider/data collection and all trading
eligibility gates. This runs locally and never modifies original NSE
`annual_historical_results.csv`, FY files, quarterly inputs, NSE sector,
broker tokens, or live order settings.

User action to build first **real-company** review queue from 80 existing
references (the exact 25 stock identities must be obtained from user's
local records, not guessed from public search):
```bash
cd ~/nse-fundamentals-monitor- && git pull && bash termux_backend.sh restart &&
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_reconcile.py &&
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_review.py
```

## Offline vendor-to-official-NSE fundamental source reconciliation — 16 October 2026

As of the owner's most recent confirmed phone output: **80/1,270** distinct
Upstox NSE_EQ vendor-reference issuers collected successfully, with
**25** automatically added during 16 October's first daily batch and **1,190**
uncollected. Existing strict pass and market/trading gates remain zero;
no evidence that actual Groww execution is available.

Added `upstox_reference_reconcile.py` (offline only, no tokens/network):
- Reads private checkpointed Upstox vendor-reference FY2024–25 `Mar 2025`
  and FY2025–26 `Mar 2026` annual data.
- Compares exactly **revenue, net profit, total assets, and operating cash
  flow**, in crore, with rows in private `annual_historical_results.csv`
  only if **audited consolidated**, fiscal March end, original official
  nsearchives NSE XBRL URL, VERIFIED parser status, and original annual
  NSE metadata/XBRL consolidation verification are present. Baseline CSV
  claims, vendor sectors, unverified revisions and incomplete source records
  are NOT treated as audited NSE evidence.
- Distinguishes `VENDOR_METRIC_MISSING`, `VERIFIED_NSE_METRIC_MISSING`,
  `NO_INDEPENDENT_VERIFIED_NSE_YEAR`, within-tolerance source reference,
  and discrepancies needing manual source review. Source revision ambiguity
  (same date, different verified XBRL URLs or numeric values) prevents
  comparisons rather than arbitrarily picking a winner.
- Engineering review threshold is **greater than both 5% of the larger
  absolute magnitude and ₹0.1 crore** (not a guarantee of accounting
  equivalence or authenticity). Disagreements may be ordinary differences
  in reported definitions, consolidated scope, revisions or an actual
  data issue. All are **source review flags**, never autogenerated trades.
- Separately writes private read-only
  `runtime/experiments/upstox-nse-historical-reference-v1/
  upstox-reference-batches-v1/reconciliation/{summary.json,
  metric_comparisons.csv}` and source digests. Does **not** overwrite
  `annual_historical_results.csv`, base FY CSVs, vendor records, or
  `nse_sector_map.csv`. **Zero promotions into strict quality passes**.
- Supervised `termux_upstox_reference_worker.py` now runs this offline
  reconciliation immediately **after each daily vendor batch**. If original
  NSE source evidence is unavailable or corrupt, daily data acquisition
  may still finish but source audit status explicitly shows unavailable;
  it never silently marks a vendor reference as verified. Discordant
  companies are counted for manual source review. The loopback status and
  `project_status.py` include independent reconciliation counts; no
  SMS/Yes-No trading approval is enabled by an audit result.

Manual offline comparison of the **already collected** vendor references:

```bash
cd ~/nse-fundamentals-monitor- && git pull
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_reconcile.py
```

The actual vendor/NSE source agreement rate must be measured with this
owner-local run; unit tests use only synthetic fixture values and cannot
substitute for real reconciliation evidence.

## Automatic read-only Upstox daily reference batches — 16 October 2026

The user approved continuing to a **bounded automatic daily reference
collection** after the successful 5/5 mapped NSE_EQ pilot. The Termux supervisor
now starts a fifth isolated process:
`termux_upstox_reference_worker.py`. It uses the already saved local private
Analytics Token in `.env`, **never prompts again**, and waits until after
17:00 IST to try **at most 25 NEW companies once per IST calendar day**.
It is completely separate from NSE official-XBRL financial maintenance,
NSE adjusted historical price maintenance and all quote/order workflows.
The broker hierarchy remains **Groww primary for future manual-approved
execution, Upstox data backup/reference only**.

A durable SQLite claim is committed **before any network call**, preventing
same-day repeated attempts on Android app restarts/crashes. Each company
checkpoint is a separate private JSON file; successful previous references
are skipped and interrupted work can continue on a *subsequent day*. The
network provider is requested serially with conservative rate delay, and
429/403/auth/network/5xx stops this run, with another opportunity the
following day. Missing/wrong-schema company records have a 24-hour
soft-error cooldown. A public vendor NSE_EQ BOD crosswalk may refresh only
after its trusted 7-day age threshold; missing/untrusted/future metadata
fails closed and the collector refuses altered previously recorded ISINs.
Manual and automatic runs share an exclusive lock; no duplicated concurrent
requests. Android must actually keep/start Termux (Termux:Boot, wake-lock
and battery policy may matter): **a phone powered off/offline cannot fetch
data; no unconditional 24/7 guarantee is possible**.

All output is local/private:
`runtime/upstox_reference_worker_status.json`,
`runtime/upstox_reference_daily_jobs.sqlite`,
and `runtime/experiments/upstox-nse-historical-reference-v1/
upstox-reference-batches-v1/records`.
`bash termux_backend.sh status` and `project_status.py` show this vendor
job separately. The user activates the new supervisor child by doing
`git pull` and `bash termux_backend.sh restart`; no extra broker login
or subscription required for the existing Upstox Analytics Token.

**No NSE-verified audited-XBRL or official-sector rows are credited from
these vendor references.** Any bank is still subject to separate
specialist ratios; historic constituents, cashflows and price actions
remain distinct source quality gates. Trading strategy approval, direct
Groww order entry, SMS Yes/No trade proposals, live orders and automated
stop-loss are **DISABLED** (no strategy has proved positive net expectancy).

## Five-of-five Upstox source coverage confirmed; 1,270-symbol batch plan — 16 October 2026

Owner's private 16 October NSE EQ/ISIN crosswalk run established:
- Both annual baseline symbols: **1,396**, of which **1,270** match distinct
  current Upstox NSE_EQ + EQ instrument ISINs; **126** unmatched. The public
  BOD universe contains **2,699** NSE_EQ instrument rows. This is a CURRENT
  vendor reference mapping, **not** historic constituents or a guarantee
  SME/mainboard classification of every symbol.
- Actual Upstox Analytics Token succeeded on all **20** read-only GETs across
  five different issuers: `RKFORGE`, `INDIANB`, `DEEDEV`,
  `PALREDTEC`, `HCG`. All five had the **four selected vendor metrics**
  (yearly revenue, profit, total assets and operating cash flow) for
  `Mar 2023` and `Mar 2024`. The profile endpoint also provided sector
  labels: Castings/Forgings/Fasteners, Bank, Engineering, IT Software,
  and Miscellaneous. Upstox vendor sectors do not qualify as official
  NSE classifications. Banks require a specialist accounting screen.
- **Zero NSE-verified audited XBRL years added**, zero official NSE sector
  rows, zero live order approvals and unchanged 4-year strict screen.

To expand cautiously without hammering API limits or restarting from scratch,
added `upstox_reference_batches.py` with **at most 25 NEW companies per
invocation**, 0.30 seconds between read-only requests, per-company atomic
JSON checkpoints and 0600 file permissions. It validates existing vendor
mapping timestamp/provenance against BOTH NSE annual baseline symbol sets.
Matching earlier 5-company pilot data can be seeded as vendor reference,
not repromoted as NSE original XBRL. Re-runs skip checkpointed companies;
soft errors are deferred for 24 hours, authentication/403/429/network/5xx
fail closed and halt that batch. Stale mapping or changed ISIN fails closed
rather than silently reassigning company financials.
This process is intentionally **manual bounded research** until repeated
user-local runs confirm integrity; it has not been connected to automated
Termux maintenance and never touches execution or the strict shortlist.

```bash
cd ~/nse-fundamentals-monitor- && git pull &&
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_batches.py --batch 25
```

Private records: `runtime/experiments/upstox-nse-historical-reference-v1/
upstox-reference-batches-v1/records`; private summary: `last_batch.json`.
Source instrument master is refreshed only with
`upstox_reference_pilot.py --sync-instruments --limit 5` if it ages out.
Avoid accidentally overwriting earlier pilot results without preserving
provenance. Broker ordering remains Groww primary, Upstox secondary data-only.

## Upstox actual pilot success; current NSE instrument mapping fallback — 16 October 2026

The owner ran `upstox_reference_pilot.py --limit 5` on their private Termux
Upstox Analytics Token. **All four read-only GET endpoints succeeded** for
RELIANCE (INE002A01018). Both Mar 2023 and Mar 2024 returned the four
*vendor-reference* observations collected by this pilot (revenue, net profit,
total assets, operating cash flow) and vendor sector `Refineries`.
Because there was no `EQUITY_L*.csv` file found under the searched phone
directories, that run tested **one** well-documented example, not five
symbols. No NSE-verified audited XBRL facts or industry sector rows were
added, and no trade/order code was used.

**Fix**: `research_monitor/upstox_instrument_master.py` now downloads the
documented public `https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz`
over one unauthenticated HTTPS GET with strict compressed/expanded byte
limits. It constructs a private, source-hashed CURRENT symbol-ISIN crosswalk
using ONLY `exchange=NSE`, `segment=NSE_EQ`, `instrument_type=EQ`,
`lot_size=1`, consistent `instrument_key` and Indian ISIN. It intersects
both private FY2024–25 and FY2025–26 baseline symbol sets, excludes
duplicate/conflicting symbols and ISINs, and writes only an isolated vendor
mapping and provenance metadata under
`runtime/experiments/upstox-nse-historical-reference-v1`.
This is **not** an NSE-official historical mainboard-membership audit.

The manual pilot has an opt-in flag `--sync-instruments` to use the newly
downloaded crosswalk and check **five different** matched NSE EQ companies;
a failed requested sync does *not* silently fall back to one RELIANCE.
Authentication token values are never printed, committed or provided to the
public BOD endpoint. Unit tests cover NSE vs BSE / SME/non-EQ filtering,
dual-baseline intersections, duplicate/ISIN conflicts, malformed or huge
GZIP, HTTP errors, and unchanged source baseline files.

```bash
cd ~/nse-fundamentals-monitor- && git pull &&
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_pilot.py --sync-instruments --limit 5
```

**Read-only exploratory research:** Upstox historic revenue/profit/assets/
operating cash flow do NOT substitute for all six original core metrics,
four years of independent audited consolidated XBRL, official NSE sector,
ROE/ROCE/FCF or source-timestamp completeness. Zero strict screening changes
and zero approved trading strategies. Continue only after real five-issuer
coverage results and source provenance review.

## Upstox NSE-only old-fundamentals coverage pilot — 16 October 2026

User raised a BSE membership concern. Official Upstox public site currently
lists BSE TM code **6155** and NSE TM code **13942**. The project's
trading/research universe is **NSE EQ mainboard only**, not BSE-listed
instruments. Upstox company-fundamental APIs are keyed by valid Indian ISIN,
not by choosing BSE vs NSE as the market venue. The already locally verified
read-only Analytics Token can call the developer Fundamentals APIs.

Added `upstox_reference_pilot.py` as a **manual 1–5 NSE EQ symbol reference
coverage check**, separate from all strict fundamental files. It looks for a
local `EQUITY_L*.csv` (NSE listed equity symbol + ISIN + EQ series) in
persistent research or Android Download. If unavailable it uses the
Upstox-documented NSE Reliance example only, not guessed mappings. Wherever
the two baseline FY24/25 and FY25/26 files are present, a source-map symbol
must occur in both. Fetches up to four read-only GET endpoints per ISIN:
company profile sector, consolidated yearly income statement, balance sheet
and cash flow. Extracts vendor-reference Mar 2023/2024 figures, rejects
wrong report type/units, stops on auth/limit HTTP blocks, and never prints a
credential. Output stays in private `runtime/experiments/upstox-nse-historical-reference-v1/report.json`.

**Absolutely NO promotion** of vendor numbers to independently verified NSE
audited/consolidated XBRL, zero official NSE sector classifications added,
no change to the 4-year strict quality gate and **zero broker orders**.
Upstox historical coverage has not been verified in the user's account until
the local pilot is executed. This is not an automated complete-fundamentals
importer and not a permission to loosen screening conditions.

```bash
cd ~/nse-fundamentals-monitor- && git pull
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python upstox_reference_pilot.py --limit 5
```

## Termux restart and real Upstox token status — 16 October 2026

Owner's actual phone-local configuration (no secrets shared):
- `upstox_token_setup.py` stored a READ-ONLY Analytics token locally, permissions 0600.
  Real `market/status/NSE` GET returned OK and `CLOSING_END`;
  known RELIANCE `market-quote/quotes` GET returned an expected schema.
  This is **endpoint authentication evidence**, NOT verified fresh live quote.
- `swing_broker_priority_check.py` confirmed Groww token **missing** and
  Upstox token **present**; after session close its quote was rejected for
  stale timestamp and invalid bid/ask. Therefore no chosen fresh provider.
- `termux_backend.sh restart` succeeded; supervisor, swing, NSE history,
  research and dashboard processes are RUNNING, wake lock TRUE, dashboard
  `http://localhost:8787` loopback MFA. Worker phase after-market, no live
  connection or automatic orders. Historical adjusted data published, 660
  old-history research eligible symbols, 222 corporate-action review symbols.
- **Major blockers remain:** annual strict PASS 0; official sector rows 0;
  quarterly collection bounded attempts; final shortlist 0; validated
  trading strategy 0; SMS provider not configured. Fast fundamental
  provisional 478 is LOWER CONFIDENCE and NOT for trading.
- To prevent misleading interpretation of hard-coded
  `upstox: INTENTIONALLY_DEFERRED` from old no-Upstox NSE archive maintenance,
  `swing_worker` now records local broker token presence separately from
  session/quote validation. `project_status.py` reports
  `upstox_token_status=CONFIGURED_READ_ONLY_NOT_LIVE_VERIFIED` when the
  worker confirms local configuration, and still restricts live orders.
  For a valid closed session status is
  `SESSION_NOT_OPEN_NO_LIVE_QUOTE`; unknown calendars stay
  `NOT_CONNECTED`, fail-closed.

Next priority: diagnose annual/sector/quarterly strict-pass blockers rather
than promote an unvalidated trading strategy or pay for Groww API immediately.
Run `NSE_DATA_DIR="$HOME/nse-fundamentals-data" python diagnose_fundamental_blockers.py`
on the local private datasets. Do not ask for the token or publish private
OHLCV/credential files.

## Broker source priority frozen — 16 October 2026

Owner requested **Groww first, Upstox second** for all future swing workflows,
including the goal of human Yes/No approval before any Groww delivery trade.
Official documentation confirms Groww paid Trading APIs support CASH data
and order management, currently ₹499 + applicable taxes/month, with
short-lived access tokens. Upstox Analytics Token is **read-only** and cannot
place/modify/cancel trades; only data backup is possible on this token.

Added `research_monitor/broker_priority.py` and
`swing_broker_priority_check.py` for explicit **read-only** checks of a
single documented NSE equity (RELIANCE) from both brokers. If both provide
fresh normalized quotes, Groww wins. If Groww is unavailable and Upstox
is fresh, Upstox can serve as **data backup only**. If valid fresh prices
diverge by more than 1%, source selection is blocked. Closed-market
stale quotes are not represented as live.

Added hidden private-token `groww_token_setup.py` preserving the already
stored Upstox token, with chmod 0600 local `.env`. Relevant synthetic
unit tests demonstrate no credential echo, no broker POST endpoints,
strict quote freshness, no silent trading fallback and no strategy
approval. Results remain **NOT LIVE**; files have not yet been executed
with actual account tokens in the owner's Termux environment. The existing
swing worker is not yet switched to a verified provider-mapped stock
watchlist by this one-stock reference health check.

**CNC swing automation blocker:** Groww official Smart OCO currently
documents `CASH/MIS` support, not `CASH/CNC`; a 1-week–5-month delivery
position cannot safely rely on unsupported OCO protection. Any future
entry/stop/exit automation must first verify CNC-valid broker protection,
reconcile actual partial fills, define offline/overnight failure handling,
and require per-proposal user approval and an independently validated
strategy. Do not spend on API subscription just to rerun the seven
negative strategies. **Zero live orders, zero approved profitable
strategies.** See `BROKER_PRIORITY.md`.

```bash
cd ~/nse-fundamentals-monitor- && git pull
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_broker_priority_check.py
```

## Regime + Momentum private research outcome — 16 October 2026

Owner ran `swing_regime_momentum_research.py --limit 100` on adjusted
Termux historical market data. Eligible universe **660**, tested
**100** hash-sampled stocks. All runs use theoretical 1% initial-stop
capital risk, estimated Groww delivery charges, whole shares and the frozen
research window. **New strategy status: REJECTED FOR TRADING;
UNVALIDATED exploratory evidence only.**

| Measure | Regime Momentum new hypothesis | VCP comparator |
|---|---:|---:|
| Signals | 5 | 46 |
| Completed trades (normal) | 3 | 19 |
| Wins / losses | 0 / 3 | 5 / 14 |
| Mean net position return, completed | -9.6177% | -2.1160% |
| Mean net pre-trade stock-account cash return, completed | -0.9641% | -0.2191% |
| Net completed profit factor | 0 | 0.6842 |
| Worst *independent-stock* marked drawdown | 1.9094% | 2.0622% |
| Normal scenario estimated charges summed across stock-account experiments | INR 203.98 | INR 1,727.68 |
| Double-cost completed trades / wins | 2 / 0 | 16 / 4 |
| Double-cost mean net position return | -10.0332% | -2.7720% |

This is a small and losing selected sample, *not* a definitive future
prediction. Historical point-in-time fundamentals were **not** applied,
historic universe composition remains survivor-biased and fees are estimates.
No rule tuning or gate loosening is authorized from these results. Keep
reproducible historical reports but do not schedule repeated frozen-window
runs. No automated trade approvals, SMS entry alerts or orders.

**Next priorities:** obtain verified point-in-time fundamental and
historical universe inputs, audit history and corporate-action flags,
pre-register a *different* prospectively tested methodology if warranted,
then collect new post-freeze paper observations with independently
verified real broker costs and portfolio-level risk constraints. Do
not mistake synthetic code-test success or one positive historical
subgroup for profitability.

## Original Regime + Momentum Confirmation v1 — research only (16 October 2026)

**Frozen before running the new strategy on private NSE history.** Added
`research_monitor/regime_momentum_rules.py`,
`swing_regime_momentum_research.py`, synthetic regression tests and
`REGIME_MOMENTUM_STRATEGY.md`.

This one original hypothesis combines >=55% equal-weight price breadth across
the history-eligible universe, >=70th-percentile 126-session relative momentum
with >10% 126-session absolute price return, rising EMA50/EMA200 trend, close
confirmation through the *previous* 15-day high, volume/traded-value
qualification, RSI/ATR guardrails, earlier 20/60-day volatility confirmation,
existing <=10%-distance stop and **1% fee-inclusive no-gap capital sizing**.
A missing breadth/rank date blocks signals. Reuses unchanged stop-first 2R/3R
exit structure and next-session guarded entries.

The private research runner compares this strategy with the already-negative
VCP breakout **on the same deterministic 100-history-symbol sample**, with
normal estimated Groww delivery charges and double-cost/slippage stress;
all stock ledgers are *independent*, not a joint portfolio. Context breadth and
rank are computed across all 1260-bar eligible symbols, which avoids using
only the sampled 100 for those context calculations, but **does NOT solve
historical survivor bias**, fundamentally missing point-in-time financials,
or prior inspection of the very same historical price window. No result,
win-rate or profitability has been claimed until private Termux runs.

```bash
cd ~/nse-fundamentals-monitor- && git pull
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_regime_momentum_research.py --limit 100
```

No candidate alerts, live trades or broker orders are connected. Existing
rejected experiments remain rejected; the new entry is also blocked from
promotion until clean independent evaluation, point-in-time data and genuine
forward paper-trading prove reproducible results net of actual costs.

## Owner-tested 1% risk exposure results — 16 October 2026

Real private phone run: 660 symbols with sufficient history and the same frozen
100-stock experiment. Hypothetical single-stock capital INR 100,000 each.
These are **not portfolio returns**. Figures below are owner-supplied
completed-trade averages normalized by *pre-trade stock-account cash*; the
drawdowns are the **worst independent stock** in the same fixed sample.

| Previously unprofitable hypothesis | Full-cash mean capital return per completed trade | Fixed 1%-risk mean | Worst individual-stock DD full cash → fixed 1% | Risk-budget breaches (fixed 1%; double-cost) |
|---|---:|---:|---:|---:|
| VCP breakout | -0.911% | -0.219% | 19.918% → 2.062% | 0; 1 |
| Mean reversion | -1.649% | -0.241% | 42.519% → 5.055% | 4; 5 |
| Custom dip reclaim | -1.759% | -0.373% | 25.169% → 4.015% | 1; 2 |

The apparent reduction is driven primarily by smaller capital at risk (median
deployed fraction: 9.338%, 12.005% and 12.486% respectively); **the
underlying signal expectancy is still negative**. Actual exit prices and gap
moves sometimes exceeded the hypothetical 1% initial-stop loss budget, even
with correct fee-aware share sizing. Double-cost stress means remained
negative. No winning strategy has been discovered and no live trading is
permitted by this experiment.

**Research disposition:** keep the original seven and custom hypotheses
as failed retrospective attempts, retain frozen private historical records,
and do not test endless loss-making parameter variations on the same already
inspected sample. Prioritize verified primary-source multi-year fundamentals,
corporate-action and dated market-history checks, a correctly dated universe,
then preregistered new entry hypotheses and genuinely fresh forward/independent
paper observations. Strict final fundamental screening remains blocked by
missing verified coverage and must not be loosened to create a positive
backtest. A lower drawdown alone does not establish trading merit.

## Fixed 1% stop-risk research — October 16, 2026

The owner's private phone output showed heavy initial-stop exits on the inspected
100-stock sample: VCP **13 initial-stop exits averaging -9.358%** versus
2 trailing-stop exits averaging +15.701%; mean reversion **76 initial**
averaging -8.067% versus 14 trailing averaging +9.244%; custom dip-reclaim
**33 initial** averaging -6.863% versus 5 trailing averaging +8.822%.
These are **conditional exit subsets**, not evidence that trailing-stop rules
cause profits. Owner output also showed nonflagged means remain negative:
VCP -0.384% across 19 completed, mean reversion -1.108% across 99,
custom -1.772% across 44. Fees were about 0.30 percentage points per
completed trade. Forensic groups and flagged historical jumps do not justify
retrospective exclusions or relaxed stops.

Added an **opt-in** `risk_fraction` to the independent single-stock
`simulate_delivery` model (unchanged default). For a fixed 1% scenario it
uses integer shares, the initial stop, estimated exit slippage, and *buy and
sell fees* to limit **projected, no-gap** loss to 1% of cash before the trade.
It does not alter strategy signals, stop prices, trailing rules or order
handling. **An actual overnight gap, market halt or real fill can exceed 1%**.
Untradeably small positions are skipped rather than bypassing fees/risk.

The new `swing_risk_sizing_research.py` compares the unchanged all-cash
execution with fixed 1% position risk and doubled costs/slippage for
VCP, mean-reversion and custom-dip on identical symbol/date windows.
Those three were selected *after* previous performance was inspected;
comparison is only about exposure, not validating their expected returns.
Runs are manual, never automatic or live, and per-stock INR 100k simulated
ledgers do **not** form a shared diversified portfolio. Code and input
hashes are retained.

```bash
cd ~/nse-fundamentals-monitor- && git pull
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_risk_sizing_research.py --limit 100
```

GitHub synthetic tests do not measure the user's real stock returns.
No strategy approval, candidate promotion or live order permission is added.
Independent forward tests and actual broker contract notes remain necessary.

## Strategy research pause and loss forensics — 16 October 2026

The owner ran private adjusted NSE history with **100 historical symbols** in the
exploratory Groww-fee scenario. All **seven** distinct price-only hypotheses
had negative pooled completed-trade mean returns. The best retrospective
mean was **VCP breakout -0.91% (20 completed trades)**; mean reversion was
**-1.64% (105)**, custom dip-reclaim **-1.77% (44)**, trend-pullback
**-2.85% (89)**, trend-following **-4.55% (62)**, momentum breakout
**-5.38% (46)**, breakout **-7.23% (14)**. These are owner-supplied output
figures, per trade and not portfolio returns. Regulatory/broker charges were
estimated from the published Groww NSE delivery schedule; personal taxes,
historical brokerage tariff changes and real fills were not verified.
**Zero verified profitable strategies; no live orders.**

Fixed retrospective experiments terminate their in-sample exploration on
2026-10-15. The three redundant scheduled tests were removed from
`termux_maintenance.STEPS` to avoid wasting the phone's cycles repeating
identical historical windows. Old private experiment reports remain available,
and **quarterly, sector, annual and readiness monitoring remains enabled**.
Manual execution of old research scripts is still possible. Fresh sessions
cannot be considered independently validated by retroactively changing
parameters on the inspected window.

Read-only diagnostic now captures each simulated trade's initial/exit stop,
opening gap below stop, whether a daily OHLC bar touched both stop and
target, trailing stop status and partial profit-taking. The stop-first
assumption and P&L calculations are unchanged. `--brief` prints a compact
loss-attribution report, and `--strategies` narrows diagnostic work without
selecting a purported winner. Example:

```bash
cd ~/nse-fundamentals-monitor- && git pull
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_loss_diagnostic.py --limit 100 --top 3 --brief --strategies vcp-breakout-v1 mean-reversion-v1 custom-dip-reclaim-v1
```

All data are read offline, output is private under
`runtime/experiments/loss-diagnostic-v1/report.json`. Do not interpret a
large >35% single-day jump as an automatic data error; investigate exchange
source and adjustments first. No strategy is promoted on this report.

## Research-only strategy comparison and ranking (16 October 2026)

- The daily Termux research maintenance now compares the same deterministic
  100-stock sample of **breakout-v1** and **trend-pullback-research-v1**, if
  private validated adjusted OHLCV is present. No GitHub runner has that
  private CSV and GitHub unit tests do **not** establish real-market returns.
- Completed-trade net results now exclude forced end-of-sample liquidations;
  original figures including those forced exits remain separately labeled.
  Normal and doubled costs/slippage and source/implementation hashes are kept.
- New `research_monitor/exploratory_ranking.py` describes a research leader
  only if **at least 30 pooled completed trades in each cost run**, **at least
  10 symbols with completed trades**, positive completed net means including
  double-cost stress, profit factor >=1.5, and worst per-stock drawdown <=20%.
  Its ranking metric is completed net mean under doubled costs. Even if these
  screening gates pass, any leader is marked **EXPLORATORY** and **NOT
  VALIDATED**. Pooled overlapping trades are not portfolio returns.
- `project_status.py` reports the exploratory leader only when both current
  input metadata hash and implementation SHA match the local experiment report.
  Stale results are blocked. Verified profitable strategies remain **0**
  and **no automatic trading, candidate promotion, SMS or order execution**
  is introduced.
- GitHub Actions unit/regression/cloud smoke checks pass for this logic using
  synthetic examples. A real 100-stock run on the private Termux OHLCV, real
  profitability result, independent fresh evaluation, point-in-time fundamental
  screening, and forward paper observations have **not** been independently
  confirmed. The user's earlier 20-stock local result (11 completed pullback
  trades) is insufficient for proof.

## Separate trend-pullback experiment — not enabled for trading

Added `swing_pullback_research.py` with frozen, explicit research-v1 rules:
close > EMA50 > EMA200, reclaim of EMA20 from the prior close at/below EMA20,
RSI 50–72, volume at least its 20-bar average, average turnover >= INR 5 crore,
and the existing ATR/stop-distance limits. Entry/exit/cost model is reused.
This is a hypothesis-driven experimental strategy, not a proven improvement.
No active strategy, candidate list, alert gate or paper plan is switched.

It compares the original and experimental strategy on the same deterministic
20-symbol sample by default (maximum 100 per invocation). Both base and doubled
cost/slippage runs are exploratory. Sampling is based on symbol hash, not returns.
Only history through 15 October 2026 is used; post-design observations are excluded.
The old holdout is already consumed for design and is never called independent
validation for the new version. Source hashes and the complete protocol are
recorded under `runtime/experiments/trend-pullback-research-v1/comparison.json`.
No future win probability, portfolio result or live pass is generated.

```sh
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_pullback_research.py --limit 20
```

This reads existing history and runs a bounded comparison, not history collection
or a full-universe backtest. More signals alone are not an investment improvement.
Point-in-time universe/fundamentals, portfolio risk, fresh evaluation data and
forward observations remain outstanding. Fundamental/sector blockers are unchanged.

## Filter-level signal investigation

Owner supplied signal audit: 660 history-eligible stocks, 273,570 holdout-day
observations, 357 signals and at most 238 eligible next-open entries ignoring
occupied positions. 475 stocks have no signals; 518 have no eligible entries;
142 have only 1–9. No stock can meet the fixed 30-completed-trade threshold on
this dataset with the current rules. This does not prove future unprofitability.

Added per-filter independent pass counts and a sequential rejection breakdown to
`swing_signal_audit.py`, plus missing indicator and zero-volume counts. Signal
conditions are shared with the backtest, not an alternative strategy. Synthetic
regression checks confirm exact equivalence with the old boolean expression,
past indicators unchanged by future bars, basic RSI edge cases, and ATR gaps.
169 local tests passed. No calculation error has been demonstrated by these tests;
actual phone filter counts are still needed. Criteria and trading behavior stay
unchanged. Compact read-only command:

```sh
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_signal_audit.py --filters-only
```

Since the existing holdout is now being inspected for strategy design, it cannot
serve as untouched validation for any future revised strategy. A new strategy
requires independent evaluation and forward observations, not threshold tuning
until the same historical stocks pass.

## Signal-entry audit and industry-source investigation

Owner diagnostics confirm all 660 history-eligible symbols have fewer than 30
completed holdout trades; 423 more lack 1,260 bars. Nonpositive mean returns can
include no-trade samples and must not be described as 614 losing strategies.
Added `swing_signal_audit.py`: reads validated local OHLCV, counts prior-bar
signals and next-open entry rejections in the unchanged 30% holdout. Counts every
potential entry irrespective of existing positions, so eligible counts are upper
bounds, never completed trades. It makes no network calls or writes and does not
run the five backtest simulations. Phone execution is still needed for real counts.

```sh
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python swing_signal_audit.py
```

The exact entry rule uses the next open plus 0.1% illustrative slippage, accepts
only prices between signal close and 1% above it, and rejects stop distance above
12%. No change to these rules or pass thresholds has been made.

Official alternative source tested on 16 October 2026:
https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv
returned HTTP 200 and 755 rows with Company Name, Industry, Symbol, Series and
ISIN Code. This offers partial classification for an index subset, not the full
four-level classification of all target companies. It has NOT been substituted
for the strict sector gate; broad labels could miss specialist-sector exceptions.
Full local regression suite including signal-audit tests: 164 tests passed.

## Termux evidence supplied by owner — 16 October 2026, around 14:06 IST

The phone's pasted status reports adjusted NSE history published for 1,083 symbols
(1,365,517 rows), covering 2020-01-01 through 2026-10-15. 660 symbols have at least
1,260 bars; no unresolved archive-session gaps were reported. 222 symbols remain
excluded for corporate-action review. These are owner-supplied runtime results,
not a fresh remote inspection of the phone or independent source audit.

All eight post-publication scripts exited successfully. This means the pipeline
completed, not that its investment criteria passed: strict annual/shortlist and
technical passes are zero. The provisional two-year screen has 478 passes but
zero current provisional candidates. Official sector rows are zero; quarterly
collection reports BOUNDED_ATTEMPT_LIMIT_REACHED. Upstox is deferred, SMS setup
pending and entry alerts blocked. Supervisor/dashboard were running with wake lock.

Termux now has separate history/research workers, loopback MFA dashboard,
crash restart, optional Termux:Boot and encrypted backup/restore commands.
Actual phone force-stop/reboot recovery and restore should not be inferred from
code tests. Render is an optional alternate host, not a prerequisite for the
phone's current research pipeline.

## Diagnostic improvement

Backtest reports previously collapsed every eligible-history rejection into
INSUFFICIENT_OR_WEAK_EVIDENCE. Future runs record every failed fixed threshold
and development/half-period metrics. Thresholds are unchanged. The new read-only
`diagnose_research.py` reports existing backtest, annual, sector and quarterly
blockers without contacting a broker or rerunning history. Older reports expose
only partial backtest evidence; the diagnostic explicitly marks missing detail.

Phone command (does not stop the backend):

```sh
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python diagnose_research.py
```

The phone's detailed reports are still needed to identify its actual rejection
counts. No source gap or zero-pass result has been declared fixed by this change.

## Latest update: cloud deployment package (not yet hosted)

- Added a single-instance Render Blueprint, supervised Gunicorn + worker startup,
  shared persistent data paths across research modules, and a minimal health probe.
- Failure of either child stops its sibling and exits for a platform restart.
  Shutdown also terminates worker subprocesses. Source snapshots refresh at deploy;
  private history, auth state and paper databases survive. No SMS is enabled.
- Offline checks now run even when the exchange calendar/Upstox is unavailable.
  The worker never infers that a market session is open from missing calendar data.
- Added private server recovery-code rotation and an operator deployment runbook.
- Verification: **108 unit/regression tests passed**. A real local Gunicorn + worker
  smoke run checked startup, HTTP guards, login, session persistence and TOTP replay
  rejection after restart, offline readiness, and sibling shutdown on worker failure.
  These checks use temporary synthetic credentials; Render itself is not verified.
- Still external: Render account/service creation and private enrollment, ingress
  and off-service backup/restore acceptance, licensed adjusted history, missing
  verified annual facts, forward observation time, risk/capital inputs, and SMS
  provider/event orchestration. Upstox activation/live testing remains deferred.

## Latest source-run result (16 October 2026)

The earlier cloud recovery run completed: 100 legacy XML candidates examined,
0 accepted annual rows; stored quarterly rows increased to 1,774 and final
shortlist stayed 0. These quarterly records were produced by the earlier
parser, so the new v6 re-verification gate must still be satisfied before they
can qualify. This result is separate from paper-engine unit tests.

## Latest update: persistent forward paper research

- Added a SQLite paper ledger with frozen plan registration timestamps, duplicate suppression and completed-session replay using the existing strategy engine.
- Registration after the next session opens is rejected. Missing calendar days/bars wait; revised historical inputs require review. Open positions remain open at the available-data boundary.
- Gap stop losses, partial targets, trailing stops and time exits follow the shared model. Results are normalized per-stock EOD observations, not portfolio simulation, real fills or live exit alerts.
- Worker archives verified calendars and runs paper/readiness checks before market and after close. Existing observations can advance even if a new screen fails. Dashboard shows timestamped paper status.
- Full local suite: **100 tests passed**. Actual run without history: `WAITING_FOR_VALID_ADJUSTED_HISTORY`, zero plans, zero real orders, forward-validation flag false.
- Still blocked by real adjusted history, verified calendar coverage, sufficient forward observation time, user capital/risk configuration, SMS provider integration and cloud deployment. No successful real-market paper performance is claimed.

## Previous update: quarterly date correctness and re-verification

- Fixed a reproduced ISO-date bug: `2025-04-01` was read as January 4. ISO dates now keep their year-month-day meaning; invalid ISO dates cannot fall back to a swapped date.
- Quarterly parser version is now 6. Stored earlier records remain available for audit but cannot contribute comparisons or final shortlist eligibility until reverified. Successful download-ledger entries alone cannot suppress a missing/current-version raw result.
- Recovery prioritizes existing rows requiring re-verification; source status reports separate stored rows, current-parser verified rows and rows still requiring re-verification.
- QoQ/YoY uses the exact previous quarter/year end, including unequal month lengths. Missing intermediate quarters no longer prevent an otherwise valid same-quarter year comparison. Missing/zero/nonfinite baselines do not generate growth percentages.
- All 90 local regression tests passed, including all four quarterly XML periods, parser migration, month-end boundaries and stale-source blocking.
- Workflow updates queue behind an active fundamentals run instead of canceling it. More verified quarterly coverage is expected only after real source collection finishes; no increased coverage or new shortlist is claimed from tests.

## Previous update: legacy annual source recovery

- Added a bounded official-XML verifier for FY2022–23/FY2023–24 and attached source/index hashes to accepted annual rows.
- Verifies company identity, exact financial-year context, audit, consolidated basis, monetary units and duplicate facts; never turns metadata alone into verified financials.
- Runs before integrated annual fetching in the daily workflow. 403/429, server and network failures stop requests; rejected filings do not overwrite history.
- Actual source check: 11 distinct official filings downloaded across two bounded local runs. All 11 lacked the exact April–March context required by the verifier; **0 new annual rows** were accepted. This is a document-context issue, not proof that annual statements do not exist. Corrected XML or independently verified annual statements are still needed.
- Full local regression suite now has 80 passing tests. Current final shortlist remains 0; no trading-readiness claim is made.

## Earlier completed offline-history update

- Offline adjusted-history CSV import, with exact-byte SHA256 matching and supplied source/adjustment attestation checks. No broker account is needed. This validates the input structure, not provider truth.
- Shared validation for imports and actual backtests: missing symbols, invalid OHLCV, duplicate daily bars, malformed/intraday dates and future bars are rejected. Current-day bars remain excluded.
- Importing new history invalidates old reports/plans. Failed screening removes old candidates and their manifest.
- Dashboard hides candidate files without a fresh manifest or when any linked historical, fundamental or backtest input changes. Plans expire from display after 24 hours; all remain conditional research.
- `python swing_readiness.py` reports local missing inputs, history coverage, matching-data technical pass count, worker freshness and unresolved operational dependencies without contacting a broker. Dashboard shows the last check timestamp.
- GitHub test workflow runs the complete unittest suite.

## Verification

Local full suite: 68 tests passed, including synthetic end-to-end history → backtest → zero eligible candidates. Existing authentication, risk, SMS and fundamental source tests also pass. Synthetic fixtures establish software behavior only; no investment performance or real SMS delivery was tested.

## Earlier evidence snapshot and remaining work

The following coverage numbers are the 15 October snapshot; use the newer run result above for 16 October:

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
| Cloud | Deployment package and local process smoke test implemented | Create actual hosting, enroll private credentials, verify ingress and backup/restore |

No orders are placed. No future success probability is calibrated. Having a token or passing unit tests does not make the system live-ready. Local Windows files are not automatically updated by a GitHub commit.

Progress in this file is a dated snapshot, not a background service or recurring notification.
