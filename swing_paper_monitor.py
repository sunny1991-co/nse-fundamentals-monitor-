"""Advance the local forward EOD research ledger without sending orders or SMS."""
import json
from pathlib import Path
from research_monitor.paper import run

if __name__=='__main__':
    result=run(Path(__file__).resolve().parent)
    print(json.dumps({k:v for k,v in result.items() if k!='plans'},indent=2))
