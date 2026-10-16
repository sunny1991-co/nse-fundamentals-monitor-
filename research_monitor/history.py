"""Offline daily-history validation. A supplied attestation is not a source audit."""
import hashlib
import io
import json
import re
from pathlib import Path
import numpy as np
import pandas as pd

COLUMNS = ['Symbol', 'Date', 'Open', 'High', 'Low', 'Close', 'Volume']


def validate_history(payload, metadata, today=None):
    digest = hashlib.sha256(payload).hexdigest()
    if not isinstance(metadata, dict) or metadata.get('sha256') != digest:
        raise ValueError('HISTORY_HASH_MISMATCH')
    if metadata.get('corporate_actions_adjusted') is not True or any(
        not isinstance(metadata.get(k), str) or not metadata[k].strip()
        for k in ('source', 'adjustment_method')
    ):
        raise ValueError('ADJUSTMENT_AND_SOURCE_ATTESTATION_REQUIRED')
    raw = pd.read_csv(io.BytesIO(payload), dtype={'Symbol': str, 'Date': str}, keep_default_na=False)
    if not set(COLUMNS).issubset(raw.columns) or raw.empty:
        raise ValueError('NONEMPTY_DAILY_OHLCV_REQUIRED')
    raw = raw[COLUMNS].copy()
    raw['Symbol'] = raw.Symbol.str.strip().str.upper()
    if not raw.Symbol.map(lambda s: bool(re.fullmatch(r'[A-Z0-9][A-Z0-9&_.-]*', s))).all():
        raise ValueError('INVALID_OR_EMPTY_SYMBOL')
    # Daily bars must be dates, not intraday or timezone-bearing timestamps.
    if not raw.Date.str.fullmatch(r'\d{4}-\d{2}-\d{2}').all():
        raise ValueError('DAILY_DATE_MUST_BE_YYYY_MM_DD')
    raw['Date'] = pd.to_datetime(raw.Date, format='%Y-%m-%d', errors='raise')
    for col in COLUMNS[2:]:
        raw[col] = pd.to_numeric(raw[col], errors='raise')
    prices = raw[COLUMNS[2:]]
    if not np.isfinite(prices).all().all():
        raise ValueError('NONFINITE_OHLCV')
    if (raw.duplicated(['Symbol', 'Date']).any()
        or (raw[['Open', 'High', 'Low', 'Close']] <= 0).any().any()
        or (raw.Volume < 0).any()
        or (raw.High < raw[['Open', 'Close', 'Low']].max(axis=1)).any()
        or (raw.Low > raw[['Open', 'Close', 'High']].min(axis=1)).any()):
        raise ValueError('INVALID_OR_DUPLICATE_OHLCV')
    today = pd.Timestamp(today) if today is not None else pd.Timestamp.now(tz='Asia/Kolkata').tz_localize(None)
    today = today.normalize()
    if (raw.Date > today).any():
        raise ValueError('FUTURE_DAILY_BAR')
    # Current-day rows are excluded even after close until session-aware ingestion exists.
    raw = raw[raw.Date < today].sort_values(['Symbol', 'Date']).reset_index(drop=True)
    if raw.empty:
        raise ValueError('NO_COMPLETED_DAILY_BARS')
    return raw, digest


def read_history(root, today=None):
    root = Path(root)
    return validate_history((root/'market_ohlcv.csv').read_bytes(),
        json.loads((root/'market_data_metadata.json').read_text(encoding='utf-8')), today)


def coverage(frame):
    rows = []
    for symbol, bars in frame.groupby('Symbol'):
        rows.append(dict(Symbol=symbol, Completed_Bars=len(bars),
            First_Date=str(bars.Date.min().date()), Last_Date=str(bars.Date.max().date()),
            Minimum_History_Met=len(bars) >= 1260))
    return rows
