"""Private interactive server operation. Never execute in a shared chat."""
import os
import secrets
import sys
from research_monitor.auth import AuthStore
from research_monitor.paths import data_root


def main():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit('A private interactive terminal is required')
    path = data_root()/'runtime'/'auth.sqlite'
    if not path.is_file():
        raise SystemExit('Start and enroll the server before rotating recovery codes')
    if input('Replace ALL server recovery codes? Type ROTATE: ') != 'ROTATE':
        raise SystemExit('Cancelled')
    store = AuthStore(path, os.environ['APP_PASSWORD_HASH'], os.environ['APP_TOTP_SECRET'])
    codes = ['recovery-'+secrets.token_urlsafe(18) for _ in range(10)]
    store.replace_recovery_codes(codes)
    print('Store these offline privately. Each requires your password:')
    print('\n'.join(codes))


if __name__ == '__main__':
    main()
