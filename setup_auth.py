"""Run interactively on YOUR trusted machine/server. Never run in a shared chat.
Creates ignored local .env and persistent auth database; shows enrollment once.
"""
import argparse
import getpass
import os
from pathlib import Path
import secrets
import pyotp
from werkzeug.security import generate_password_hash
from research_monitor.auth import AuthStore

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',required=True,help='Exact HTTPS app hostname, without scheme')
    parser.add_argument('--db',required=True,help='Absolute path on persistent private storage')
    args=parser.parse_args()
    if '/' in args.host or not args.host or not Path(args.db).is_absolute():
        raise SystemExit('Exact hostname and absolute persistent DB path required')
    if Path('.env').exists() or Path(args.db).exists():
        raise SystemExit('Existing configuration detected. Refusing to overwrite enrollment.')
    password=getpass.getpass('New app password (at least 14 characters): ')
    if len(password)<14 or password!=getpass.getpass('Confirm password: '):
        raise SystemExit('Password too short or confirmation mismatch')
    seed=pyotp.random_base32()
    print('Add a time-based key to your Authenticator. Do not share this key:')
    print(seed)
    otp=getpass.getpass('Enter current 6-digit code to confirm enrollment: ')
    if not pyotp.TOTP(seed).verify(otp,valid_window=1):
        raise SystemExit('Enrollment not verified; no configuration saved')
    password_hash=generate_password_hash(password,method='scrypt')
    codes=['recovery-'+secrets.token_urlsafe(18) for _ in range(10)]
    auth=AuthStore(args.db,password_hash,seed)
    auth.replace_recovery_codes(codes)
    values={'APP_SECRET_KEY':secrets.token_urlsafe(48),'APP_PASSWORD_HASH':password_hash,
        'APP_TOTP_SECRET':seed,'APP_AUTH_DB':args.db,'APP_HOST':args.host}
    fd=os.open('.env',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        for key,value in values.items():
            f.write(f'{key}={value}\n')
    print('Saved private .env. Import values into server secret settings; never commit them.')
    print('Save these single-use recovery codes offline; each also requires your password:')
    print('\n'.join(codes))

if __name__=='__main__': main()
