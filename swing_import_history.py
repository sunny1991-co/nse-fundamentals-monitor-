"""Import an already adjusted, source-attested CSV without contacting a broker."""
import argparse
import json
from pathlib import Path
from research_monitor.history import validate_history, coverage
from research_monitor.schedule import atomic_status

ROOT = Path(__file__).resolve().parent


def import_history(csv_path, metadata_path, root=ROOT):
    root = Path(root)
    payload = Path(csv_path).read_bytes()
    meta = json.loads(Path(metadata_path).read_text(encoding='utf-8'))
    frame, digest = validate_history(payload, meta)
    root.mkdir(parents=True, exist_ok=True)
    # Validation occurs before any existing dataset/output is changed.
    # Install bytes first, then metadata: a crash between them fails the hash gate.
    temp = root/'market_ohlcv.csv.tmp'
    temp.write_bytes(payload)
    temp.replace(root/'market_ohlcv.csv')
    atomic_status(root/'market_data_metadata.json', meta)
    for name in ('swing_backtest_report.csv', 'swing_backtest_trades.csv',
                 'swing_trade_candidates.csv', 'swing_candidates_manifest.json'):
        (root/name).unlink(missing_ok=True)
    report = dict(data_sha256=digest, symbols=coverage(frame),
        scope='STRUCTURE_AND_SUPPLIED_ATTESTATION_ONLY_NOT_PROVIDER_AUDIT')
    atomic_status(root/'runtime'/'history_import.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', required=True)
    parser.add_argument('--metadata', required=True)
    args = parser.parse_args()
    try:
        report = import_history(args.csv, args.metadata)
    except (OSError, ValueError, TypeError):
        raise SystemExit('Import blocked: check file access, OHLCV format, source/adjustment attestation and exact CSV SHA256.') from None
    eligible = sum(r['Minimum_History_Met'] for r in report['symbols'])
    print(f"Imported {len(report['symbols'])} symbols; {eligible} meet the 1260-bar minimum. No strategy pass implied.")


if __name__ == '__main__':
    main()
