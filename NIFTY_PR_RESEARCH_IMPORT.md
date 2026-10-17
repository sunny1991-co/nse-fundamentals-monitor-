# NIFTY Price Return CSV — isolated research-only import

Use for manually downloaded Historical Data > Price Return (PR) files for
NIFTY 50 or NIFTY 100 with six columns: Index Name, Date, Open, High, Low, Close.

This is NOT the separate Total returns Index Values export and NOT gross TRI.
NIFTY 50 TRI-gated signal logic is unchanged and remains blocked without
reviewed genuine total-return data.

The importer:
- Selects only exact matching filename prefix for the selected index;
- Validates index identity, required OHLC columns, dates, and numeric prices;
- Chronologically merges and rejects conflicting overlapping dates;
- Writes a CSV and SHA-256 source receipt to private NSE_DATA_DIR;
- Refuses replacing existing output unless explicitly requested and validates
  existing research-only metadata before an explicit refresh;
- Never implies official origin from a filename or enables brokerage orders.

Run on Termux after merging this PR:

    cd ~/nse-fundamentals-monitor-
    git pull --ff-only
    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nifty_price_return_import.py --index 'NIFTY 50' --source-dir "$HOME/storage/shared/Download"

For the six existing NIFTY 100 PR files in the phone Downloads folder:

    NSE_DATA_DIR="$HOME/nse-fundamentals-data" python nifty_price_return_import.py --index 'NIFTY 100' --source-dir "$HOME/storage/shared/Download"

Private outputs:
- nifty50_pr_history.csv + nifty50_pr_history_metadata.json
- nifty100_pr_history.csv + nifty100_pr_history_metadata.json

Refresh only when needed with --replace-research-only after inspecting new
exports; the importer checks existing research metadata for tampering.

Validation:

    python -m unittest -v test_nifty_price_return_import.py

Owner supplied six NIFTY 50 PR files spanning 19 Oct 2020 to 16 Oct 2026,
yielding 1,488 validated, non-overlapping OHLC records. This is user-supplied
source content; independently verified official provenance is not asserted.
Raw downloaded CSV data stays private and is not committed to GitHub.

Hard constraints: no gross TRI promotion, no price-NIFTY substitution into
strict relative-strength or quality-leader benchmark gate, no fundamentals
downgrade, no profit validation claim, no live or paper orders from importer.
