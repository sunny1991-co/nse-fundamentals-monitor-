"""Read existing phone reports; no network, no writes and no expensive backtest."""
import csv
import json
from collections import Counter
from pathlib import Path
from research_monitor.paths import data_root


def rows(root, name):
    try:
        with (root/name).open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))
    except FileNotFoundError:
        return []


def counted(values):
    return dict(Counter(values).most_common())


def snapshot(root):
    root = Path(root)
    backtest = rows(root, 'swing_backtest_report.csv')
    annual = rows(root, 'annual_fundamental_quality_report.csv')
    sectors = rows(root, 'nse_sector_map.csv')
    quarterly = rows(root, 'quarterly_collection_status.csv')
    exact, legacy, known = [], 0, []
    for row in backtest:
        if row.get('Backtest_Pass', '').lower() == 'true':
            continue
        reasons = row.get('Failure_Reasons', '')
        if reasons:
            exact.extend(set(filter(None, reasons.split('|'))))
        elif row.get('Reason') == 'LESS_THAN_1260_BARS':
            exact.append('LESS_THAN_1260_BARS')
        else:
            legacy += 1
            # Older reports expose holdout metrics but omit development/halves.
            # Count only directly observable failures; never invent the rest.
            for field, reason, passes in [
                ('Completed_Trades', 'HOLDOUT_COMPLETED_TRADES_LT_30', lambda n: n >= 30),
                ('Mean_Return', 'HOLDOUT_MEAN_RETURN_NOT_POSITIVE', lambda n: n > 0),
                ('Max_Drawdown', 'HOLDOUT_DRAWDOWN_GT_20_PERCENT', lambda n: n <= .20),
                ('Stress_Mean_Return', 'STRESS_MEAN_RETURN_NOT_POSITIVE', lambda n: n > 0),
            ]:
                try:
                    if not passes(float(row[field])):
                        known.append(reason)
                except (KeyError, ValueError, TypeError):
                    pass
    blockers = []
    for row in annual:
        blockers.extend(set(filter(None, row.get('Full_Screen_Blockers', '').split('|'))))
    return {
        'scope': 'READ_ONLY_EXISTING_REPORTS_NOT_LIVE_VERIFICATION',
        'backtest_rows': len(backtest),
        'backtest_report_missing': not (root/'swing_backtest_report.csv').exists(),
        'backtest_pass': sum(r.get('Backtest_Pass', '').lower() == 'true' for r in backtest),
        'exact_failure_counts_overlapping': counted(exact),
        'legacy_rows_without_full_failure_details': legacy,
        'legacy_observable_failure_counts_overlapping': counted(known),
        'annual_blockers_overlapping': counted(blockers),
        'sector_lookup_status': counted(r.get('Lookup_Status', 'UNKNOWN') for r in sectors),
        'sector_error_counts': counted(r.get('Error', '')[:200] for r in sectors if r.get('Lookup_Status') != 'OK'),
        'quarterly_collection_status': quarterly[-1] if quarterly else {},
        'note': 'Counts overlap. Old reports cannot explain development/half-period failures; next normal backtest will add them. No thresholds changed.',
    }


if __name__ == '__main__':
    print(json.dumps(snapshot(data_root()), indent=2))
