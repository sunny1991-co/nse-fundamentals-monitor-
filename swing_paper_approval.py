"""Local paper rehearsal CLI. Never sends broker orders or uses API keys."""
import argparse
import json
from research_monitor.paths import data_root
from research_monitor.paper_approval import PaperApprovals, BROKER_PREFERENCE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    create = sub.add_parser('create')
    for name in ('symbol', 'strategy'):
        create.add_argument('--' + name, required=True)
    create.add_argument('--broker', choices=BROKER_PREFERENCE, default='GROWW')
    for name in ('capital', 'entry-min', 'entry-max', 'stop', 'target', 'fee-fraction'):
        create.add_argument('--' + name, required=True, type=float)
    show = sub.add_parser('show')
    show.add_argument('id')
    decide = sub.add_parser('decide')
    decide.add_argument('id')
    decide.add_argument('--digest', required=True)
    decide.add_argument('--answer', required=True, choices=('yes', 'no'))
    observe = sub.add_parser('observe')
    observe.add_argument('id')
    observe.add_argument('--price', type=float, required=True)
    observe.add_argument('--observed-at', type=float, required=True,
                         help='UTC Unix timestamp of the paper price observation')
    args = vars(parser.parse_args())
    command = args.pop('command')
    if command == 'status':
        result = dict(mode='PAPER_REHEARSAL_ONLY', live_orders='DISABLED',
            broker_preference=BROKER_PREFERENCE, automatic_broker_failover=False,
            strategy_validation='NO_APPROVED_STRATEGY_REGISTERED',
            dashboard_approval='NOT_CONNECTED', automatic_price_feed='NOT_CONNECTED',
            blockers=['UNVALIDATED_STRATEGIES', 'ORDER_ADAPTERS_NOT_IMPLEMENTED',
                      'DELIVERY_PROTECTIVE_EXITS_AND_FILL_RECONCILIATION_NOT_IMPLEMENTED'])
    else:
        ledger = PaperApprovals(data_root() / 'runtime' / 'paper_approvals.sqlite3')
        try:
            if command == 'create':
                result = ledger.create(**args)
            elif command == 'show':
                result = ledger.get(args['id'])
            elif command == 'decide':
                result = ledger.decide(args['id'], args['digest'], args['answer'])
            else:
                result = ledger.observe(args['id'], args['price'], args['observed_at'])
        except ValueError as exc:
            parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
