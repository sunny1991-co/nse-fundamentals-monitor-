"""Secure, local-only Groww Trading API token setup for read-only checks.

The Groww official access token expires daily at 06:00 IST, unlike Upstox
Analytics Token. Never print tokens or put them in chat, GitHub or command args.
No broker POST call or order execution lives in this file.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path

from dotenv import load_dotenv, set_key

from upstox_token_setup import _valid_token, private_env_path
from research_monitor.broker_priority import dual_quote_check
from research_monitor.paths import data_root
from research_monitor.schedule import atomic_status

KEY = "GROWW_ACCESS_TOKEN"
GENERATED_KEY = "GROWW_TOKEN_SAVED_UTC"


def save_groww_token(path, token, saved_at=None):
    path=Path(path)
    if not _valid_token(token):
        raise ValueError("GROWW_TOKEN_FORMAT_INVALID_NO_SECRET_LOGGED")
    if path.is_symlink():
        raise ValueError("REFUSE_SYMLINKED_ENV")
    if path.exists():
        if not path.is_file() or path.stat().st_uid != os.getuid():
            raise ValueError("ENV_NOT_REGULAR_OR_NOT_OWNED")
        os.chmod(path,0o600)
    else:
        fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.close(fd)
    try:
        set_key(str(path),KEY,token,quote_mode="always")
        set_key(str(path),GENERATED_KEY,
                saved_at or datetime.now(timezone.utc).isoformat(),
                quote_mode="always")
    finally:
        os.chmod(path,0o600)
    return {
        "saved":True,
        "private":True,
        "file_permissions":"0600",
        "token_type":"GROWW_TRADING_API_ACCESS",
        "token_expires":"DAILY_AT_06_00_IST_PER_GROWW_DOCUMENTATION",
        "order_execution":"DISABLED",
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check",action="store_true",
                        help="Use already-stored token, no secret prompt")
    parser.add_argument("--offline",action="store_true",
                        help="Save Groww token without external GET calls")
    args=parser.parse_args(argv)
    path=private_env_path()
    if not args.check:
        print("Paste Groww API token ONLY at this hidden Termux prompt.")
        try:
            token=getpass.getpass("Groww API token (hidden): ")
        except (EOFError,KeyboardInterrupt):
            print("\nTOKEN_NOT_SAVED")
            return 2
        try:
            info=save_groww_token(path,token)
            print(json.dumps(info,indent=2))
        except (ValueError,OSError):
            print("TOKEN_NOT_SAVED: invalid token format or local file checks")
            return 2
        finally:
            del token
        if args.offline:
            return 0
    load_dotenv(path,interpolate=False,override=True)
    result=dual_quote_check()
    path=data_root()/"runtime"/"broker_priority_check.json"
    atomic_status(path,result)
    print(json.dumps(result,indent=2))
    print("Read-only data check, no real orders, no trade approvals.")
    print("Fresh quotes cannot be established when the market is closed.")
    return 0 if result["groww"]["quote_status"] == "OK_FRESH" else 1


if __name__=="__main__":
    raise SystemExit(main())
