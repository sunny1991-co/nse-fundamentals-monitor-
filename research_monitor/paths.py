"""Keep mutable research data on an explicitly configured persistent volume."""
import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent.parent


def data_root():
    value = os.environ.get('NSE_DATA_DIR')
    if not value:
        return CODE_ROOT
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError('NSE_DATA_DIR must be an absolute path')
    return path.resolve()
