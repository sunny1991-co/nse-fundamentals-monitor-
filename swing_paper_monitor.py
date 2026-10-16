"""Advance the local forward EOD research ledger without sending orders or SMS."""
from research_monitor.paths import data_root
import json
from pathlib import Path
from research_monitor.paper import run
from research_monitor.forward_experiment import run as run_experiment

if __name__=='__main__':
    result=run(data_root())
    experiment=run_experiment(data_root())
    result['automatic_forward_experiment']={k:v for k,v in experiment.items() if k!='plans'}
    print(json.dumps({k:v for k,v in result.items() if k!='plans'},indent=2))
