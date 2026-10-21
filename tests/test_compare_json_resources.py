import csv
import json
import tempfile
import unittest
from pathlib import Path

from compare_json_resources import compare


class JSONComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.single, self.multi = self.root / 'single', self.root / 'multi'

    def create(self, directory, records):
        (directory / 'outputs').mkdir(parents=True)
        with (directory / 'results.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['file', 'run-id', 'fhir-valid'])
            writer.writeheader()
            for run, valid, resources in records:
                writer.writerow({'file': 'a.txt', 'run-id': run, 'fhir-valid': valid})
                if valid:
                    (directory / 'outputs' / f'{run}_a.json').write_text(json.dumps({
                        'resourceType': 'Bundle', 'type': 'collection',
                        'entry': [{'resource': r} for r in resources]}))

    def test_field_counts_mapping_and_run_sd(self):
        patient = {'resourceType': 'Patient', 'id': 'patient-1', 'gender': 'female'}
        expanded = dict(patient, communication=[{'language': {'text': 'English'}}])
        self.create(self.single, [(1, 1, [patient]), (4, 1, [patient])])
        self.create(self.multi, [(1, 1, [patient]), (3, 1, [expanded])])
        counts, fields, runs, summary, cases, meta = compare(self.single, self.multi, {4: 3})
        added = next(r for r in fields if r['field'] == 'communication')
        self.assertEqual(added['single_occurrences'], 0)
        self.assertEqual(added['multi_occurrences'], 1)
        result = next(r for r in summary if r['method'] == 'multi' and
                      r['resource_type'] == 'Patient' and r['metric'] == 'properties_total')
        self.assertEqual(result['mean'], 1.5)
        self.assertAlmostEqual(result['sd'], 2 ** -.5)
        self.assertEqual(meta['runs'], [1, 3])
        self.assertTrue(any(r['single_original_run'] == 4 and r['run'] == 3 for r in counts))

    def test_failed_outputs_zero_and_optional_paired_filter(self):
        patient = {'resourceType': 'Patient', 'id': 'patient-1'}
        self.create(self.single, [(1, 1, [patient]), (2, 1, [patient])])
        self.create(self.multi, [(1, 0, []), (2, 1, [patient])])
        counts, *_ = compare(self.single, self.multi)
        row = next(r for r in counts if r['run'] == 1 and r['resource_type'] == 'Patient')
        self.assertEqual(row['multi_resources'], 0)
        with self.assertRaisesRegex(ValueError, 'No paired-valid cases'):
            compare(self.single, self.multi, paired_valid=True)

    def test_mismatched_runs_rejected(self):
        self.create(self.single, [(4, 1, [])])
        self.create(self.multi, [(3, 1, [])])
        with self.assertRaisesRegex(ValueError, 'differ'):
            compare(self.single, self.multi)
