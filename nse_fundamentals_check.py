
import pandas as pd
from pathlib import Path

FILES = {
    "FY2024-25": "FY2425.csv",
    "FY2025-26": "NSE_FY2025-26_Consolidated_Fundamentals.csv",
}

REQUIRED_FIELDS = [
    "Revenue_Operations_Cr",
    "Net_Profit_Cr",
    "Basic_EPS_Rs",
    "Total_Assets_Cr",
    "Total_Equity_Cr",
    "Total_Borrowings_Cr",
]

for year, filename in FILES.items():
    path = Path(filename)
    print(f"\n{'=' * 50}\n{year}: {filename}")

    if not path.exists():
        print("FILE MISSING")
        continue

    df = pd.read_csv(path)
    print("Rows:", len(df))
    print("Columns:", list(df.columns))

    missing_cols = [c for c in REQUIRED_FIELDS if c not in df.columns]
    if missing_cols:
        print("Required columns not found:", missing_cols)
        print("Column names must be mapped before strict checking.")
        continue

    for col in REQUIRED_FIELDS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    complete = df[REQUIRED_FIELDS].notna().all(axis=1)
    print("All six fields populated:", int(complete.sum()))
    print("Incomplete rows:", int((~complete).sum()))

    df["Six_Fields_Complete"] = complete
    output = filename.replace(".csv", "_checked.csv")
    df.to_csv(output, index=False)
    print("Saved:", output)

print("\nCheck finished.")
