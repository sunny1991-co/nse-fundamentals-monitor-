"""Private interactive recovery-code rotation. Never execute in a shared chat."""
import os
from pathlib import Path
import secrets
import sys

from dotenv import load_dotenv

CODE_ROOT = Path(__file__).resolve().parent
load_dotenv(CODE_ROOT/'.env', interpolate=False)

from research_monitor.auth import AuthStore


def auth_db_path(env=None):
    env = os.environ if env is None else env
    value = env.get('APP_AUTH_DB', '')
    if not value:
        raise ValueError('APP_AUTH_DB is missing from private configuration')
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError('APP_AUTH_DB must be an absolute path')
    return path.resolve()


def main():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit('A private interactive terminal is required')
    try:
        path = auth_db_path()
    except ValueError as exc:
        raise SystemExit(str(exc))
    if not path.is_file():
        raise SystemExit('Auth database not found; verify private enrollment first')
    if not os.environ.get('APP_PASSWORD_HASH') or not os.environ.get('APP_TOTP_SECRET'):
        raise SystemExit('Private password/TOTP configuration is incomplete')
    if input('Replace ALL recovery codes and invalidate active sessions? Type ROTATE: ') != 'ROTATE':
        raise SystemExit('Cancelled')
    store = AuthStore(path, os.environ['APP_PASSWORD_HASH'], os.environ['APP_TOTP_SECRET'])
    codes = ['recovery-'+secrets.token_urlsafe(18) for _ in range(10)]
    store.replace_recovery_codes(codes)
    print('Store these offline privately. Old recovery codes and active sessions are now invalid:')
    print('\n'.join(codes))


if __name__ == '__main__':
    main()
