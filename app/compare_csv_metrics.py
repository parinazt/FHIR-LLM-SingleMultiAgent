"""Compare CSV success/attempt metrics using runs as the statistical unit.

No third-party packages or API calls are required. Source CSVs are never changed.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

AGENTS = ('patient', 'condition', 'observation', 'medication', 'merge')


def read_runs(path, method, selected_runs=None):
    metrics = ['fhir-valid', 'attempts']
    if method == 'multi':
        metrics.extend(f'{agent}-attempts' for agent in AGENTS)
    groups = defaultdict(list)
    seen = set()
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        missing = {'file', 'run-id', *metrics} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f'{path}: missing columns: {sorted(missing)}')
        for line, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'{path}:{line}: incomplete or malformed CSV row')
            try:
                run = int(row['run-id'])
            except ValueError as exc:
                raise ValueError(f'{path}:{line}: invalid run-id') from exc
            if selected_runs is not None and run not in selected_runs:
                continue
            key = (run, row['file'])
            if key in seen:
                raise ValueError(f'{path}: duplicate file/run {key}; resolve repeated outcomes explicitly')
            seen.add(key)
            groups[run].append(row)
    if selected_runs is not None and set(groups) != set(selected_runs):
        raise ValueError(f'{path}: missing requested runs: {sorted(set(selected_runs) - set(groups))}')
    if not groups:
        raise ValueError(f'{path}: no selected runs')
    per_run = []
    for run, rows in sorted(groups.items()):
        for metric in metrics:
            values = []
            for row in rows:
                text = row[metric].strip()
                if not text:
                    continue  # Unknown is not zero; record the actual denominator.
                try:
                    value = float(text)
                except ValueError as exc:
                    raise ValueError(f'{path}: run {run}, {metric}: invalid number {text!r}') from exc
                if not math.isfinite(value) or (metric == 'fhir-valid' and value not in (0, 1)):
                    raise ValueError(f'{path}: run {run}, {metric}: invalid value {text!r}')
                if metric != 'fhir-valid' and (value < 0 or not value.is_integer()):
                    raise ValueError(f'{path}: run {run}, {metric}: attempts must be nonnegative integers')
                values.append(value)
            per_run.append({
                'method': method, 'run_id': run, 'metric': metric,
                'n_cases': len(rows), 'n_observed': len(values),
                'n_missing': len(rows) - len(values),
                'run_mean': statistics.mean(values) if values else None,
            })
    return per_run


def summarize(per_run):
    groups = defaultdict(list)
    for row in per_run:
        groups[(row['method'], row['metric'])].append(row)
    result = []
    for (method, metric), rows in groups.items():
        values = [row['run_mean'] for row in rows if row['run_mean'] is not None]
        mean = statistics.mean(values) if values else None
        sd = statistics.stdev(values) if len(values) > 1 else None
        result.append({
            'method': method, 'metric': metric, 'n_runs': len(rows),
            'n_runs_observed': len(values), 'mean': mean, 'sd': sd,
            'mean_percent': mean * 100 if mean is not None and metric == 'fhir-valid' else None,
            'sd_percentage_points': sd * 100 if sd is not None and metric == 'fhir-valid' else None,
        })
    return result


def write_csv(path, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def format_value(value):
    return 'NA' if value is None else f'{value:.4f}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--single-csv', type=Path, required=True)
    parser.add_argument('--multi-csv', type=Path, required=True)
    parser.add_argument('--single-runs', type=int, nargs='+', help='Optional subset of single run IDs')
    parser.add_argument('--multi-runs', type=int, nargs='+', help='Optional subset of multi run IDs')
    parser.add_argument('--output-dir', type=Path, default=Path('results/csv_metrics'))
    args = parser.parse_args()
    try:
        per_run = [*read_runs(args.single_csv, 'single', args.single_runs),
                   *read_runs(args.multi_csv, 'multi', args.multi_runs)]
        summary = summarize(per_run)
        # Never overwrite either input file through the chosen report directory.
        targets = [args.output_dir / name for name in ('per_run.csv', 'summary.csv', 'report.json')]
        if any(target.resolve() in (args.single_csv.resolve(), args.multi_csv.resolve()) for target in targets):
            raise ValueError('Report files must not overwrite source CSVs')
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_csv(args.output_dir / 'per_run.csv', per_run)
        write_csv(args.output_dir / 'summary.csv', summary)
        report = {
            'sources': {'single': str(args.single_csv.resolve()), 'multi': str(args.multi_csv.resolve())},
            'aggregation': 'Unweighted mean and sample SD (ddof=1) of per-run means. '
                           'SD is undefined for fewer than two observed runs.',
            'success': 'Mean of fhir-valid (0/1); percentage SD is expressed in percentage points.',
            'attempts': 'CSV attempts column as recorded; not the sum of all LLM calls.',
            'agent_attempts': 'Mean over all cases in each run, including zero for agents not invoked.',
            'missing': 'Blank values excluded, never converted to zero; denominators in per_run.csv.',
            'summary': summary,
        }
        (args.output_dir / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for method in ('single', 'multi'):
        run_ids = sorted({r['run_id'] for r in per_run if r['method'] == method})
        print(f'\n{method.upper()} - runs: {run_ids}')
        for row in summary:
            if row['method'] != method:
                continue
            if row['metric'] == 'fhir-valid':
                print(f"  Success: {format_value(row['mean_percent'])}% +/- "
                      f"{format_value(row['sd_percentage_points'])} percentage points")
            else:
                print(f"  {row['metric']}: {format_value(row['mean'])} +/- {format_value(row['sd'])}")
    for row in per_run:
        if row['n_missing']:
            print(f"WARNING: {row['method']} run {row['run_id']}, {row['metric']}: "
                  f"{row['n_missing']} missing values excluded")
    print(f'\nReport: {args.output_dir.resolve()}')


if __name__ == '__main__':
    main()
