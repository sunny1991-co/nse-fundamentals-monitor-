# NSE official industry reference — a separate, non-tradable dataset

As of 17 October 2026, the NSE quotation API used for the four-level company
sector classification returns HTTP 403 in the Termux run. This is an access
failure, not evidence that every company has an invalid business.

NSE Indices publishes a separate **Nifty Total Market** constituent CSV:

https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv

This index covers about 750 stocks and its public constituent CSV contains a
coarse **Industry** label. The industry labels are authoritative only as
reported for the index at fetch time. They do **not** establish the company's
NSE Indices four-tier classification: macro-sector, sector, industry, basic
industry. Not all 1,747 NSE companies in the strict annual study belong to
this index. Membership is NOT evidence of 2022–26 historical eligibility.

`nse_index_industry_reference.py` performs exactly one bounded download from
this official archive, validates the CSV schema, detects malformed or duplicate
symbols, and records the SHA256 and fetch time. It writes only:

- `$NSE_DATA_DIR/nse_totalmarket_industry_reference.csv`
- `$NSE_DATA_DIR/runtime/nse_totalmarket_industry_reference_status.json`

A blocked request, unexpected redirect or malformed source preserves the
previous downloaded reference and reports its current fetch failure explicitly.
The daily Termux research maintenance runs this reference fetch automatically.
No source is silently treated as current after an access failure.

**Hard boundary:** this source is never written to `nse_sector_map.csv`, and
does not change annual screening, quarterly screening, technical screening,
paper approval, trade eligibility, or the verified-trading count. All existing
strict criteria remain unchanged.

Manual read-only run:

```bash
cd ~/nse-fundamentals-monitor-
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_index_industry_reference.py
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python diagnose_fundamental_blockers.py
```

NSE Indices official methodology:
https://www.niftyindices.com/resources/industry-classification

At least four independently verified audited consolidated annual periods and
current official company-level sector mapping are still needed for a strict
eligible shortlist. Verified 2024–25 and 2025–26 data cannot be relabeled as
a verified four-year CAGR.
