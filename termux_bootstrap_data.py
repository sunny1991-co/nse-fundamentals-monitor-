"""Seed the private Termux data directory from tracked public CSV snapshots.

Existing private/local data is never overwritten. This keeps mutable runtime and
research outputs out of the Git working tree so future pulls stay clean.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def seed(code_root: Path, data_root: Path) -> dict:
    code_root = Path(code_root).resolve()
    data_root = Path(data_root).expanduser().resolve()
    if data_root == code_root or code_root in data_root.parents:
        raise ValueError("Termux data directory must be outside the Git repository")
    data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (data_root/'runtime').mkdir(parents=True, exist_ok=True, mode=0o700)
    copied = []
    skipped = []
    for source in sorted(code_root.glob('*.csv')):
        target = data_root/source.name
        if target.exists():
            skipped.append(source.name)
            continue
        shutil.copy2(source, target)
        copied.append(source.name)
    return {'data_root': str(data_root), 'copied': copied, 'skipped_existing': skipped}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--data-root', type=Path, required=True)
    args = parser.parse_args()
    result = seed(args.code_root, args.data_root)
    print(f"Termux data directory: {result['data_root']}")
    print(f"Seeded public CSV snapshots: {len(result['copied'])}; existing kept: {len(result['skipped_existing'])}")


if __name__ == '__main__':
    main()
