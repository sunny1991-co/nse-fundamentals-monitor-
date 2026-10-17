"""Import owner-provided NIFTY 50/100 PRICE RETURN CSVs for isolated research.

Never install this data as NIFTY 50 gross TRI or attest an official source.
Read-only local research data; cannot place trades or unblock TRI-gated signals.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile

SUPPORTED = {'NIFTY 50': 'nifty50_pr_history', 'NIFTY 100': 'nifty100_pr_history'}
HEADER = ['Index Name', 'Date', 'Open', 'High', 'Low', 'Close']
MAX_FILES = 40
MAX_BYTES = 2_000_000
MAX_ROWS = 7500
SOURCE_FIRST_DATE = date(2010, 1, 1)
TYPE = 'USER_SUPPLIED_NIFTY_PRICE_RETURN_RESEARCH_ONLY'


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def parse_csv(blob: bytes, expected_index: str, *, today: date):
    if not 0 < len(blob) <= MAX_BYTES:
        raise ValueError('PR_FILE_SIZE_INVALID')
    try:
        reader = csv.DictReader(io.StringIO(blob.decode('utf-8-sig'), newline=''))
        if reader.fieldnames != HEADER:
            raise ValueError('PR_OHLC_HEADER_REQUIRED_NOT_TRI')
        rows = {}
        for r in reader:
            if None in r or any(v is None for v in r.values()):
                raise ValueError('PR_ROW_MALFORMED')
            if r['Index Name'].strip() != expected_index:
                raise ValueError('PR_WRONG_INDEX')
            try:
                day = datetime.strptime(r['Date'].strip(), '%d %b %Y').date()
            except ValueError:
                raise ValueError('PR_DATE_INVALID') from None
            if not SOURCE_FIRST_DATE <= day <= today:
                raise ValueError('PR_DATE_OUT_OF_RANGE')
            try:
                values = tuple(Decimal(r[k].replace(',', '').strip()) for k in HEADER[2:])
            except (InvalidOperation, TypeError):
                raise ValueError('PR_PRICE_INVALID') from None
            if not all(v.is_finite() and 0 < v < 10**9 for v in values):
                raise ValueError('PR_PRICE_INVALID')
            o, h, l, c = values
            if h < max(o, c, l) or l > min(o, c, h):
                raise ValueError('PR_OHLC_INCONSISTENT')
            if day in rows:
                raise ValueError('PR_DUPLICATE_DATE_WITHIN_FILE')
            rows[day] = values
            if len(rows) > MAX_ROWS:
                raise ValueError('PR_TOO_MANY_ROWS')
    except UnicodeError:
        raise ValueError('PR_FILE_ENCODING_INVALID') from None
    if not rows:
        raise ValueError('PR_EMPTY_FILE')
    return rows


def csv_bytes(rows, index):
    out = io.StringIO(newline='')
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(HEADER)
    for day in sorted(rows):
        writer.writerow([index, day.isoformat(), *(format(v, 'f') for v in rows[day])])
    return out.getvalue().encode('utf-8')


def atomic_write(path: Path, payload: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.nifty_pr_', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def import_dir(source_dir, out_dir, index='NIFTY 50', *, today=None,
               replace_research_only=False):
    if index not in SUPPORTED:
        raise ValueError('PR_INDEX_UNSUPPORTED')
    src, dest = Path(source_dir), Path(out_dir)
    today = today or datetime.now(timezone.utc).date()
    paths = sorted(src.glob(f'{index}_Historical_PR_*.csv'))
    if not 1 <= len(paths) <= MAX_FILES:
        raise ValueError('PR_SOURCE_FILE_COUNT_INVALID')
    rows = {}
    originals = []
    overlaps = 0
    for p in paths:
        if not p.is_file() or p.is_symlink():
            raise ValueError('PR_SOURCE_MUST_BE_REGULAR_FILE')
        raw = p.read_bytes()
        incoming = parse_csv(raw, index, today=today)
        for day, values in incoming.items():
            if day in rows:
                if rows[day] != values:
                    raise ValueError('PR_OVERLAPPING_VALUES_CONFLICT')
                overlaps += 1
            else:
                rows[day] = values
        originals.append({'file_name': p.name, 'sha256': sha(raw),
                          'rows': len(incoming), 'first': min(incoming).isoformat(),
                          'last': max(incoming).isoformat()})
    if len(rows) > MAX_ROWS:
        raise ValueError('PR_TOO_MANY_ROWS')
    payload = csv_bytes(rows, index)
    stem = SUPPORTED[index]
    out_csv = dest / (stem + '.csv')
    out_meta = dest / (stem + '_metadata.json')
    if out_csv.exists() or out_meta.exists():
        if not replace_research_only:
            raise ValueError('PR_RESEARCH_OUTPUT_EXISTS_USE_EXPLICIT_REPLACE')
        if not out_csv.is_file() or not out_meta.is_file():
            raise ValueError('PR_EXISTING_PAIR_INCOMPLETE')
        try:
            meta = json.loads(out_meta.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError):
            raise ValueError('PR_EXISTING_METADATA_INVALID') from None
        if (meta.get('type') != TYPE or meta.get('index_name') != index
                or meta.get('sha256') != sha(out_csv.read_bytes())
                or meta.get('operator_source_checked') is not False):
            raise ValueError('PR_EXISTING_RESEARCH_INTEGRITY_INVALID')
    receipt = {
        'type': TYPE, 'index_name': index, 'return_kind': 'PRICE_RETURN_NOT_TRI',
        'operator_source_checked': False,
        'source_origin': 'OWNER_PROVIDED_CSV_NOT_INDEPENDENTLY_ATTESTED',
        'sha256': sha(payload), 'rows': len(rows),
        'first_date': min(rows).isoformat(), 'last_date': max(rows).isoformat(),
        'source_files': originals, 'overlapping_identical_dates': overlaps,
        'sufficient_1260_price_days': len(rows) >= 1260,
        'eligible_for_tri_strategy_benchmark': False,
        'profitability_verified': False, 'live_orders': 'DISABLED',
    }
    atomic_write(out_csv, payload)
    atomic_write(out_meta, (json.dumps(receipt, indent=2) + '\n').encode('utf-8'))
    return {'state': 'PRICE_RETURN_RESEARCH_ONLY_IMPORTED',
            'index_name': index, 'rows': len(rows),
            'first_date': receipt['first_date'], 'last_date': receipt['last_date'],
            'output_csv': str(out_csv), 'output_metadata': str(out_meta),
            'tri_benchmark': False, 'live_orders': 'DISABLED'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', choices=tuple(SUPPORTED), default='NIFTY 50')
    parser.add_argument('--source-dir', required=True)
    parser.add_argument('--output-dir', default=os.environ.get('NSE_DATA_DIR',
                        str(Path.home() / 'nse-fundamentals-data')))
    parser.add_argument('--replace-research-only', action='store_true')
    args = parser.parse_args()
    try:
        result = import_dir(args.source_dir, args.output_dir, args.index,
                            replace_research_only=args.replace_research_only)
    except (OSError, ValueError) as err:
        result = {'state': 'PRICE_RETURN_RESEARCH_IMPORT_BLOCKED',
                  'reason_code': str(err) if str(err).startswith('PR_') else 'PR_UNREADABLE_INPUT',
                  'tri_benchmark': False, 'live_orders': 'DISABLED'}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
