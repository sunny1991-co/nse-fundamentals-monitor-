"""Safely probe both verified NSE equity quote APIs: Groww first, Upstox second.

Read-only. NO order calls. Must NOT send credentials in chat or GitHub.
Loads existing ignored private local .env from code directory.
"""
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent/".env", interpolate=False, override=True)

from research_monitor.broker_priority import dual_quote_check
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status


def main():
    report=dual_quote_check()
    path=data_root()/"runtime"/"broker_priority_check.json"
    atomic_status(path,report)
    print(json.dumps(report,indent=2))
    print("Private broker-data report:",path)
    print("Market-closed timestamps can be stale: do not treat a logged-in API "
          "as an actionable live stock quote.")
    print("The check sends NO orders, accepts NO trade approval and does NOT "
          "switch the execution account.")


if __name__=="__main__":
    main()
