"""Compare FHIR resource types and top-level fields, aggregating across runs.

Structural comparison only: does not match clinical facts or score their meaning.
"""
import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from .compare_results import TYPES, load_results, remap_runs, write_csv


def load_inventory(directory, mapping):
    # Reject repeated outcomes rather than hiding failed attempts with later successes.
    seen = set()
    with (directory / 'results.csv').open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            key = (row['file'], int(row['run-id']))
            if key in seen:
                raise ValueError(f'{directory}: duplicate file/run outcome {key}')
            seen.add(key)
    data = remap_runs(load_results(directory), mapping)
    inverse = {}
    for file, original_run in seen:
        run = mapping.get(original_run, original_run)
        inverse[(file, run)] = original_run
    fields = {}
    for key, result in data.items():
        inventory = {kind: Counter() for kind in TYPES}
        if result['valid']:
            file, _ = key
            bundle = json.loads((directory / 'outputs' /
                                f'{inverse[key]}_{Path(file).stem}.json').read_text(encoding='utf-8'))
            for entry in bundle['entry']:
                resource = entry['resource']
                inventory[resource['resourceType']].update(set(resource) - {'resourceType', 'id'})
        fields[key] = inventory
    return data, fields, inverse


def mean_sd(values):
    return statistics.mean(values), statistics.stdev(values) if len(values) > 1 else None


def compare(single_dir, multi_dir, single_map=None, paired_valid=False):
    S, SF, SI = load_inventory(single_dir, single_map or {})
    M, MF, MI = load_inventory(multi_dir, {})
    if S.keys() != M.keys():
        raise ValueError('Case/run pairs differ between methods. Align IDs with --single-run-map '
                         '(e.g. 4:3) and supply complete matching runs.')
    if not S:
        raise ValueError('No case/run pairs to compare')
    keys = sorted(k for k in S if not paired_valid or (S[k]['valid'] and M[k]['valid']))
    all_runs = sorted({run for _, run in S})
    if set(run for _, run in keys) != set(all_runs):
        raise ValueError('No paired-valid cases for at least one run')
    counts, fields = [], []
    totals = defaultdict(Counter)
    for key in keys:
        file, run = key
        for kind in (*TYPES, 'ALL'):
            a, b = S[key]['counts'][kind], M[key]['counts'][kind]
            counts.append({
                'file': file, 'run': run, 'single_original_run': SI[key], 'multi_original_run': MI[key],
                'single_valid': int(S[key]['valid']), 'multi_valid': int(M[key]['valid']),
                'resource_type': kind, 'single_resources': a['resources'], 'multi_resources': b['resources'],
                'delta_resources_multi_minus_single': b['resources'] - a['resources'],
                'single_properties': a['properties'], 'multi_properties': b['properties'],
                'delta_properties_multi_minus_single': b['properties'] - a['properties'],
            })
            for method, item in (('single', a), ('multi', b)):
                totals[(run, method, kind)].update(item)
            if kind != 'ALL':
                for field in sorted(set(SF[key][kind]) | set(MF[key][kind])):
                    a_count, b_count = SF[key][kind][field], MF[key][kind][field]
                    fields.append({'file': file, 'run': run, 'resource_type': kind, 'field': field,
                                   'single_occurrences': a_count, 'multi_occurrences': b_count,
                                   'delta_multi_minus_single': b_count - a_count})
    per_run = []
    for (run, method, kind), values in sorted(totals.items()):
        n = sum(r == run for _, r in keys)
        per_run.append({'run': run, 'method': method, 'resource_type': kind, 'n_cases': n,
                        'resources_total': values['resources'], 'properties_total': values['properties'],
                        'resources_mean_per_case': values['resources'] / n,
                        'properties_mean_per_case': values['properties'] / n})
    summary = []
    for method in ('single', 'multi'):
        for kind in (*TYPES, 'ALL'):
            rr = [r for r in per_run if r['method'] == method and r['resource_type'] == kind]
            for metric in ('resources_total', 'properties_total', 'resources_mean_per_case', 'properties_mean_per_case'):
                mean, sd = mean_sd([r[metric] for r in rr])
                summary.append({'method': method, 'resource_type': kind, 'metric': metric,
                                'n_runs': len(rr), 'mean': mean, 'sd': sd})
    case_summary = []
    grouped = defaultdict(list)
    for row in counts:
        grouped[(row['file'], row['resource_type'])].append(row)
    for (file, kind), rr in sorted(grouped.items()):
        for method in ('single', 'multi'):
            for metric in ('resources', 'properties'):
                mean, sd = mean_sd([r[f'{method}_{metric}'] for r in rr])
                case_summary.append({'file': file, 'resource_type': kind, 'method': method,
                                     'metric': metric, 'n_runs': len(rr), 'mean': mean, 'sd': sd})
    metadata = {'single_dir': str(single_dir.resolve()), 'multi_dir': str(multi_dir.resolve()),
                'single_run_map': single_map or {}, 'runs': all_runs, 'n_case_run_pairs': len(keys),
                'paired_valid_only': paired_valid,
                'properties': 'Top-level resource fields excluding resourceType and id. '
                              'Each object/array counts once; nested fields and Bundle/fullUrl excluded.',
                'aggregation': 'Mean and sample SD (ddof=1) across per-run totals or per-run case means. '
                               'Case summary SD is across runs for the same case and resource type.',
                'failures': 'Recorded failed/invalid outputs contribute zero counts unless --paired-valid is used. '
                            'Missing case/run records are rejected.',
                'limitations': 'Field occurrences are not unique clinical facts. No semantic comparison, '
                               'resource matching by ID, or correctness scoring is performed.'}
    return counts, fields, per_run, summary, case_summary, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--single-dir', type=Path, required=True)
    parser.add_argument('--multi-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('results/json_comparison'))
    parser.add_argument('--single-run-map', default='', help='Report-only ID mapping, e.g. 4:3')
    parser.add_argument('--paired-valid', action='store_true', help='Include only pairs valid in both methods')
    args = parser.parse_args()
    try:
        if any(args.output_dir.resolve() == p.resolve() or args.output_dir.resolve() in p.resolve().parents
               for p in (args.single_dir, args.multi_dir)):
            raise ValueError('The report directory must not contain or replace either input directory')
        mapping = dict(tuple(map(int, p.split(':'))) for p in args.single_run_map.split(',') if p)
        *tables, metadata = compare(args.single_dir, args.multi_dir, mapping, args.paired_valid)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        names = ('case_run_counts.csv', 'field_occurrences.csv', 'per_run.csv', 'summary.csv', 'case_summary.csv')
        for name, table in zip(names, tables):
            if table:
                write_csv(args.output_dir / name, table)
            else:
                (args.output_dir / name).write_text('file,run,resource_type,field,single_occurrences,multi_occurrences,delta_multi_minus_single\n', encoding='utf-8')
        (args.output_dir / 'report.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(f'Runs: {metadata["runs"]}; case/run pairs: {metadata["n_case_run_pairs"]}')
    print(f'Report: {args.output_dir.resolve()}')


if __name__ == '__main__':
    main()
