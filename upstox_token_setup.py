"""Safely install an Upstox read-only data token into LOCAL ignored .env.

No token is sent to ChatGPT/GitHub, printed, added to shell history, or
included in user-visible logs. This script NEVER calls order endpoints.

Usage on Android Termux:
    python upstox_token_setup.py                 # hidden prompt, then safe GET check
    python upstox_token_setup.py --kind standard # daily-expiring OAuth access token
    python upstox_token_setup.py --check         # existing private token only
    python upstox_token_setup.py --offline       # save only; no network check
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re

from dotenv import load_dotenv, set_key

from research_monitor.upstox_data import DataUnavailable, UpstoxData

ENV_KEY = "UPSTOX_ANALYTICS_TOKEN"  # existing backend expects this variable
TOKEN_KIND_KEY = "UPSTOX_DATA_TOKEN_KIND"
TOKEN_TIMESTAMP_KEY = "UPSTOX_DATA_TOKEN_SAVED_UTC"
RELIANCE_KEY = "NSE_EQ|INE002A01018"
KINDS = ("analytics", "standard")


def private_env_path(code_root=None):
    root = Path(code_root or Path(__file__).resolve().parent)
    return root / ".env"


def _valid_token(token):
    # Never show the invalid value. JWTs are accepted; raw tokens may vary.
    return (isinstance(token, str)
            and 20 <= len(token) <= 8192
            and not re.search(r"[\s\x00-\x1f\x7f]", token)
            and not token.lower().startswith("bearer "))


def store_token(path, token, kind="analytics", *, saved_at=None):
    """Preserve other .env variables, store only to local private .env.

    Tests explicitly verify no token disclosure on either return value or
    error; no other files (including GitHub or SQLite) are written here.
    """
    path = Path(path)
    if kind not in KINDS:
        raise ValueError("TOKEN_KIND_MUST_BE_ANALYTICS_OR_STANDARD")
    if not _valid_token(token):
        raise ValueError("TOKEN_FORMAT_NOT_ACCEPTED_NO_VALUE_LOGGED")
    if path.is_symlink():
        raise ValueError("REFUSE_SYMLINKED_ENV")
    if path.exists() and not path.is_file():
        raise ValueError("ENV_PATH_NOT_A_FILE")
    if path.exists() and path.stat().st_uid != os.getuid():
        raise ValueError("ENV_FILE_NOT_OWNED_BY_CURRENT_USER")
    if path.exists():
        os.chmod(path, 0o600)
    else:
        # Close TOCTOU window; exclusive creation and user-only permission.
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    try:
        for key, value in (
            (ENV_KEY, token),
            (TOKEN_KIND_KEY, kind),
            (TOKEN_TIMESTAMP_KEY,
                saved_at or datetime.now(timezone.utc).isoformat()),
        ):
            # dotenv rewrites the existing .env retaining other settings.
            set_key(str(path), key, value, quote_mode="always")
    finally:
        os.chmod(path, 0o600)
    return {
        "saved": True,
        "secret_storage": "LOCAL_IGNORED_ENV",
        "file_permissions": "0600",
        "token_type": kind,
        "warning": (
            "Standard OAuth access tokens expire frequently; re-run "
            "setup when 401, preferably generate a read-only Analytics Token."
            if kind == "standard" else
            "Analytics tokens are read-only, but still require private storage."
        ),
    }


def connectivity_report(adapter=None):
    """Read-only status and single-NSE-equity quote probes, fail closed.

    A closed market may return old price timestamps: quote availability
    never proves that the feed is live or suitable for entry signals.
    """
    adapter = adapter or UpstoxData()
    report = {
        "scope": "UPSTOX_READ_ONLY_DATA_CONNECTION_CHECK",
        "token_present": bool(adapter.token),
        "market_status_endpoint": "NOT_CHECKED",
        "exchange_market_status": None,
        "known_nse_equity_quote_endpoint": "NOT_CHECKED",
        "known_equity": "RELIANCE",
        "live_quote_verified": False,
        "orders": "DISABLED",
        "entry_alerts": "BLOCKED_PENDING_STRATEGY_VALIDATION",
    }
    if not adapter.token:
        report["market_status_endpoint"] = "TOKEN_MISSING"
        return report
    try:
        market = adapter._read("market/status/NSE")
        if not isinstance(market, dict) or market.get("exchange") != "NSE":
            report["market_status_endpoint"] = "UNEXPECTED_RESPONSE_SCHEMA"
            return report
        report["market_status_endpoint"] = "OK"
        report["exchange_market_status"] = str(market.get("status", "UNKNOWN"))[:40]
    except DataUnavailable as exc:
        report["market_status_endpoint"] = str(exc)
        return report
    try:
        data = adapter._read(
            "market-quote/quotes", {"instrument_key": RELIANCE_KEY}
        )
        if not isinstance(data, dict):
            report["known_nse_equity_quote_endpoint"] = "UNEXPECTED_RESPONSE_SCHEMA"
        elif (any(isinstance(x, dict)
                and x.get("instrument_token") == RELIANCE_KEY
                and isinstance(x.get("last_price"), (int, float))
                and not isinstance(x.get("last_price"), bool)
                and 0 < float(x["last_price"]) < float("inf")
                for x in data.values())):
            report["known_nse_equity_quote_endpoint"] = "OK"
        else:
            report["known_nse_equity_quote_endpoint"] = "INSTRUMENT_OR_PRICE_NOT_VERIFIED"
    except DataUnavailable as exc:
        report["known_nse_equity_quote_endpoint"] = str(exc)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=KINDS, default="analytics",
                        help="analytics: 1-year data token; standard: ordinary OAuth")
    parser.add_argument("--check", action="store_true",
                        help="No changes, check existing token using GET endpoints")
    parser.add_argument("--offline", action="store_true",
                        help="Only store the token without contacting Upstox")
    args = parser.parse_args(argv)
    path = private_env_path()
    if not args.check:
        print("Paste token at the hidden local Termux prompt. It will NOT be displayed.")
        try:
            value = getpass.getpass("Upstox token (input hidden): ")
        except (EOFError, KeyboardInterrupt):
            print("\nTOKEN_NOT_SAVED")
            return 2
        try:
            print(json.dumps(store_token(path, value, args.kind), indent=2))
        except (ValueError, OSError):
            print("TOKEN_NOT_SAVED: local file, ownership or token-format check failed.")
            return 2
        finally:
            del value
        if args.offline:
            print("Token stored. Restart existing backend to read updated .env.")
            return 0
    load_dotenv(path, override=True)
    report = connectivity_report()
    report["configured_token_type"] = os.environ.get(
        TOKEN_KIND_KEY, "UNKNOWN_NOT_CONFIRMED"
    )
    print(json.dumps(report, indent=2))
    print("The quote probe is NOT a live-feed freshness test.")
    print("Current trading and entry alerts stay DISABLED.")
    print("Restart backend separately: bash termux_backend.sh restart")
    return 0 if (
        report["market_status_endpoint"] == "OK"
        and report["known_nse_equity_quote_endpoint"] == "OK"
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
