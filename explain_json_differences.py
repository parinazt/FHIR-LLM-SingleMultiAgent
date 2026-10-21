"""Diagnose structural differences between single and multi FHIR outputs.

No API calls or clinical judgments. Review candidates include the source note.
"""
import argparse
import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from compare_json_resources import load_inventory
from compare_results import TYPES, write_csv


def normalize_text(text):
    return ' '.join(text.casefold().split())


def profile(resources):
    counts = Counter(resources=len(resources), properties=0, scaffold=0, components=0,
                     value_nodes=0, value_string_chars=0, exact_duplicates=0)
    signatures, labels, fields = Counter(), set(), Counter()
    for resource in resources:
        fields.update(set(resource) - {'resourceType', 'id'})
        counts['properties'] += len(set(resource) - {'resourceType', 'id'})
        counts['scaffold'] += sum(k in resource for k in ('status', 'code', 'subject'))
        components = resource.get('component', [])
        counts['components'] += len(components)
        for node in [resource, *components]:
            counts['value_nodes'] += sum(k.startswith('value') for k in node)
            counts['value_string_chars'] += len(node.get('valueString', ''))
        signature = {k: v for k, v in resource.items() if k != 'id'}
        signatures[json.dumps(signature, sort_keys=True, ensure_ascii=False)] += 1
        label = resource.get('code', {}).get('text')
        if label:
            labels.add(normalize_text(label))
    counts['exact_duplicates'] = sum(n - 1 for n in signatures.values())
    return dict(counts), labels, fields


def read_resources(directory, file, original_run, valid):
    result = {kind: [] for kind in TYPES}
    if valid:
        bundle = json.loads((directory / 'outputs' / f'{original_run}_{Path(file).stem}.json')
                            .read_text(encoding='utf-8'))
        for entry in bundle['entry']:
            resource = entry['resource']
            result[resource['resourceType']].append(resource)
    return result


def decompose(single, multi, shared):
    """Separate the count difference exactly into paired-output and failure effects."""
    total = multi - single
    paired = total if shared else 0
    return total, paired, total - paired


def analyze(single_dir, multi_dir, output_dir, dataset_dir, mapping=None, sample_size=3, seed=42):
    S, _, SI = load_inventory(single_dir, mapping or {})
    M, _, MI = load_inventory(multi_dir, {})
    if not S or S.keys() != M.keys():
        raise ValueError('Expected the same case/run records in both methods; align IDs with --single-run-map')
    rows, label_rows, field_rows = [], [], []
    totals = defaultdict(Counter)
    pools = defaultdict(list)
    for key in sorted(S):
        file, run = key
        shared = S[key]['valid'] and M[key]['valid']
        sr = read_resources(single_dir, file, SI[key], S[key]['valid'])
        mr = read_resources(multi_dir, file, MI[key], M[key]['valid'])
        all_s, all_m = [], []
        for kind in (*TYPES, 'ALL'):
            a = sr[kind] if kind != 'ALL' else all_s
            b = mr[kind] if kind != 'ALL' else all_m
            if kind != 'ALL':
                all_s.extend(a)
                all_m.extend(b)
            sp, sl, sf = profile(a)
            mp, ml, mf = profile(b)
            row = {'file': file, 'run': run, 'resource_type': kind,
                   'single_valid': int(S[key]['valid']), 'multi_valid': int(M[key]['valid']),
                   'paired_valid': int(shared)}
            for metric in sp:
                row[f'single_{metric}'] = sp[metric]
                row[f'multi_{metric}'] = mp[metric]
            for metric in ('resources', 'properties'):
                delta, paired, failure = decompose(sp[metric], mp[metric], shared)
                row[f'delta_{metric}'] = delta
                row[f'paired_output_effect_{metric}'] = paired
                row[f'unpaired_success_effect_{metric}'] = failure
            rows.append(row)
            aggregate = totals[(run, kind)]
            aggregate['cases'] += 1
            aggregate['paired_cases'] += shared
            for k, v in row.items():
                if k.startswith(('single_', 'multi_', 'delta_', 'paired_output_effect_', 'unpaired_success_effect_')):
                    aggregate[k] += v
            if shared and kind != 'ALL':
                for label in sorted(sl | ml):
                    label_rows.append({'file': file, 'run': run, 'resource_type': kind,
                                       'normalized_label': label, 'single_present': int(label in sl),
                                       'multi_present': int(label in ml)})
                for field in sorted(set(sf) | set(mf)):
                    field_rows.append({'file': file, 'run': run, 'resource_type': kind,
                                       'field': field, 'single_occurrences': sf[field],
                                       'multi_occurrences': mf[field]})
                direction = 'multi_more' if len(b) > len(a) else 'single_more' if len(a) > len(b) else 'equal_count'
                pools[(kind, direction)].append(row)
    runs = [{'run': run, 'resource_type': kind, **dict(values)}
            for (run, kind), values in sorted(totals.items())]
    summary = []
    for kind in (*TYPES, 'ALL'):
        rr = [r for r in runs if r['resource_type'] == kind]
        for metric in rr[0]:
            if metric in ('run', 'resource_type'):
                continue
            values = [r[metric] for r in rr]
            summary.append({'resource_type': kind, 'metric': metric, 'n_runs': len(values),
                            'mean': statistics.mean(values),
                            'sd': statistics.stdev(values) if len(values) > 1 else None})
    rng = random.Random(seed)
    candidates = []
    for (kind, direction), pool in sorted(pools.items()):
        # Include both extremes and reproducible random cases; this is exploratory,
        # not a representative estimate of clinical errors.
        extreme = sorted(pool, key=lambda r: (-abs(r['delta_resources']), r['file'], r['run']))[:sample_size]
        random_rows = rng.sample(pool, min(sample_size, len(pool)))
        chosen = {(r['file'], r['run']) for r in [*extreme, *random_rows]}
        for file, run in sorted(chosen):
            row = next(r for r in pool if r['file'] == file and r['run'] == run)
            candidates.append({**row, 'direction': direction,
                               'selection': 'extreme_and_random' if row in extreme and row in random_rows
                               else 'extreme' if row in extreme else 'random'})
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / 'case_diagnostics.csv', rows)
    write_csv(output_dir / 'per_run.csv', runs)
    write_csv(output_dir / 'summary.csv', summary)
    if label_rows:
        write_csv(output_dir / 'label_presence.csv', label_rows)
    if field_rows:
        write_csv(output_dir / 'field_presence.csv', field_rows)
    if candidates:
        write_csv(output_dir / 'review_candidates.csv', candidates)
    with (output_dir / 'review_examples.jsonl').open('w', encoding='utf-8') as stream:
        for candidate in candidates:
            key = candidate['file'], candidate['run']
            source = dataset_dir / key[0]
            kind = candidate['resource_type']
            item = {**candidate, 'source_note': source.read_text(encoding='utf-8') if source.exists() else None,
                    'single_original_run': SI[key], 'multi_original_run': MI[key],
                    'single_resources_json': read_resources(single_dir, key[0], SI[key], True)[kind],
                    'multi_resources_json': read_resources(multi_dir, key[0], MI[key], True)[kind],
                    'review_questions': ['Are the same source facts aggregated differently?',
                                         'Is a fact absent, or represented elsewhere in the Bundle?',
                                         'Are repeated resources redundant or distinct supported events?',
                                         'Are additional fields explicitly supported by the note?']}
            stream.write(json.dumps(item, ensure_ascii=False) + '\n')
    metadata = {'runs': sorted({run for _, run in S}), 'single_run_map': mapping or {},
                'seed': seed, 'samples_per_direction_and_type': sample_size,
                'definitions': {
                    'delta': 'Multi minus single. Failed/invalid outputs count as zero.',
                    'decomposition': 'delta = paired_output_effect + unpaired_success_effect. '
                                     'The first term uses cases valid in both; the second uses cases valid in only one.',
                    'properties': 'Top-level fields except resourceType/id; arrays and objects count once.',
                    'scaffold': 'Occurrences of status, code and subject. These repeat with resource splitting.',
                    'value_nodes': 'value[x] fields at resource and component levels; not a clinical fact count.',
                    'exact_duplicates': 'Identical JSON resources after removing only the root id; '
                                        'flags structural repetition, not proven clinical duplication.',
                    'labels': 'Normalized code.text string presence; different strings may describe the same fact.',
                    'review': 'Extremes plus seeded random cases in each direction/type. Exploratory, not representative.'},
                'limitations': 'No semantic validation. An absent field/label is not proof of missing information. '
                               'More resources, fields or value nodes do not establish better clinical coverage.'}
    (output_dir / 'report.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    report_lines = ['# Structural difference diagnostics', '',
                    'Mean ± sample SD across run totals. Positive differences favor multi in count only.', '',
                    '| Type | Resource delta | Paired-output effect | Unpaired-success effect |',
                    '|---|---:|---:|---:|']
    for kind in (*TYPES, 'ALL'):
        cells = []
        for metric in ('delta_resources', 'paired_output_effect_resources', 'unpaired_success_effect_resources'):
            r = next(r for r in summary if r['resource_type'] == kind and r['metric'] == metric)
            cells.append(f"{r['mean']:.2f} ± {r['sd']:.2f}" if r['sd'] is not None else f"{r['mean']:.2f} (SD unavailable)")
        report_lines.append(f'| {kind} | ' + ' | '.join(cells) + ' |')
    report_lines += ['', 'The decomposition is arithmetic, not a causal explanation. Inspect review_examples.jsonl '
                     'against source notes before attributing differences to omissions, grouping or unsupported additions.']
    (output_dir / 'report.md').write_text('\n'.join(report_lines) + '\n', encoding='utf-8')
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--single-dir', type=Path, required=True)
    parser.add_argument('--multi-dir', type=Path, required=True)
    parser.add_argument('--single-run-map', default='')
    parser.add_argument('--dataset-dir', type=Path, default=Path('dataset'))
    parser.add_argument('--output-dir', type=Path, default=Path('results/difference_diagnostics'))
    parser.add_argument('--sample-size', type=int, default=3)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    if args.sample_size < 1:
        parser.error('--sample-size must be positive')
    try:
        if any(args.output_dir.resolve() == p.resolve() or args.output_dir.resolve() in p.resolve().parents
               for p in (args.single_dir, args.multi_dir, args.dataset_dir)):
            raise ValueError('Report directory must not contain or replace input directories')
        mapping = dict(tuple(map(int, pair.split(':'))) for pair in args.single_run_map.split(',') if pair)
        metadata = analyze(args.single_dir, args.multi_dir, args.output_dir, args.dataset_dir,
                           mapping, args.sample_size, args.seed)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(f'Runs: {metadata["runs"]}; report: {args.output_dir.resolve()}')


if __name__ == '__main__':
    main()
