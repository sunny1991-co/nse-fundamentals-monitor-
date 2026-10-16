"""Daily verified NSE EOD audit of owner-approved simulated holdings."""
from __future__ import annotations
import json
from research_monitor.paths import data_root
from research_monitor.assistant_eod_reconcile import reconcile_paper_eod


def main():
    print(json.dumps(reconcile_paper_eod(data_root()),indent=2))


if __name__=="__main__":
    main()
