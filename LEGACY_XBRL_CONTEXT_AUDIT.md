# FY 2022–23 / FY 2023–24 original XBRL root-cause audit

As of 2026-10-17, the annual evidence ledger has no independently verified
2022–23/2023–24 company rows, despite 1,277 / 1,400 index-metadata
company-year candidates. The last bounded 100 original-XML attempts returned
zero verified facts, with repeated
\`EXACT_APRIL_MARCH_CONTEXT_NOT_VERIFIED\`.

This is a **composite error**, not proof that every filing omitted an audited
annual financial statement. It can also result from an unmatched NSE issuer
entity scheme, a nonmatching XML period, or a dimensional context that the
strict verifier rightly rejects.

The new \`nse_legacy_context_diagnostic.py\` makes no verification decisions.
It downloads at most **four** previously known *official* NSE XML candidate
URLs, selected from original index metadata, and separately reports:

- An exact April 1–March 31 XML duration and March 31 balance-sheet instant
- Whether exact contexts carry the matching NSE symbol/entity-identifier scheme
- Whether those contexts have other dimensional axes, standalone/mixed basis
- Source-stated consolidated label and XML symbol consistency
- A summary category explaining which basic check failed

It does not print the embedded issuer identifiers, arbitrary URL schemes,
financial numbers, actual shareholdings, or broker credentials.

**No fundamental facts or trades are added**, even when the context diagnostic
finds a promising example. For true verification, source issuer, period,
reporting basis, audit label, monetary units, disallowed axes, full numeric
consistency, filing revision and point-in-time provenance must all pass the
separately audited verifier. Do not weaken these checks to make counts rise.

Termux, manual read-only diagnostic (no real orders):

\`\`\`bash
cd ~/nse-fundamentals-monitor- && git pull --ff-only
NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nse_legacy_context_diagnostic.py --max-files 4
\`\`\`

Private result:
\`$HOME/nse-fundamentals-data/runtime/legacy_xbrl_context_diagnostic.json\`.
Official source access failures stop the run; 403/429 is not bypassed.

Once a source format difference is identified, assess its legitimacy against
actual XML taxonomies before making any verifier changes. Existing strict
screens remain blocked and do not consume diagnostic findings as trade signals.
