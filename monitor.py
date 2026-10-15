"""Explicit read-only CLI; no real orders or IPO applications."""
import argparse
import json
from pathlib import Path
from datetime import datetime,timezone
from research_monitor.market_data import SmartAPIReadOnly,FeedUnavailable
from research_monitor.ipo import review_ipo

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=['quotes','ipo-review','status'])
    parser.add_argument('--input',type=Path)
    args=parser.parse_args()
    output=Path('runtime');output.mkdir(mode=0o700,exist_ok=True)
    if args.operation=='quotes':
        destination=output/'quotes.json'
        destination.unlink(missing_ok=True)
        if not args.input:
            raise SystemExit('Provide a verified exchange-token mapping with --input')
        tokens=json.loads(args.input.read_text())
        try:
            rows=SmartAPIReadOnly().quotes(tokens)
        except (FeedUnavailable,ValueError) as exc:
            print(str(exc));raise SystemExit(1)
        destination.write_text(json.dumps({'fetched_at':datetime.now(timezone.utc).isoformat(),'quotes':rows},indent=2))
        print('Quotes saved privately. Freshness and execution eligibility must still be checked.')
    elif args.operation=='ipo-review':
        if not args.input:
            raise SystemExit('Provide independently verified RHP review JSON with --input')
        rows=json.loads(args.input.read_text())
        (output/'ipo_reviews.json').write_text(json.dumps([review_ipo(r) for r in rows],indent=2))
        print('Research checks saved. No IPO applications submitted.')
    else:
        print(json.dumps({'live_orders':'NOT_IMPLEMENTED','swing':'HISTORICAL_FEED_AND_VALIDATION_REQUIRED',
            'fno':'CONTRACT_DATA_AND_PAPER_VALIDATION_REQUIRED','ipo':'VERIFIED_RHP_INPUT_REQUIRED',
            'security':'PASSWORD_AND_AUTHENTICATOR_REQUIRED_AT_APP_STARTUP'},indent=2))

if __name__=='__main__': main()
