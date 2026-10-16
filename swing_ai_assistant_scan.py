"""Run strict source-gated EOD swing assistant watchlist; no order requests."""
from __future__ import annotations

import json

from research_monitor.assistant_signal_pipeline import scan
from research_monitor.paths import data_root


def main():
    report=scan(data_root())
    print(json.dumps({
        k:report.get(k) for k in (
            "scope","state","strategy","watchlist_count",
            "current_verified_fundamental_symbols","trade_orders",
        )
    },indent=2))


if __name__=="__main__":
    main()
