import csv
import json
import unittest

import test_compare_json_resources as fixtures
from explain_json_differences import analyze, decompose, profile


class DifferenceTests(unittest.TestCase):
    setUp = fixtures.JSONComparisonTests.setUp
    create = fixtures.JSONComparisonTests.create

    def test_profile_granularity_and_exact_repetition(self):
        resource = {'resourceType': 'Observation', 'id': 'one', 'status': 'final',
                    'code': {'text': 'Blood pressure'}, 'subject': {'reference': 'Patient/patient-1'},
                    'component': [{'valueQuantity': {'value': 120}}, {'valueQuantity': {'value': 80}}]}
        counts, labels, fields = profile([resource, dict(resource, id='two')])
        self.assertEqual(counts['resources'], 2)
        self.assertEqual(counts['scaffold'], 6)
        self.assertEqual(counts['value_nodes'], 4)
        self.assertEqual(counts['properties'], 8)
        self.assertEqual(counts['exact_duplicates'], 1)

    def test_difference_decomposition(self):
        self.assertEqual(decompose(10, 12, True), (2, 2, 0))
        self.assertEqual(decompose(10, 0, False), (-10, 0, -10))
        self.assertEqual(decompose(0, 4, False), (4, 0, 4))

    def test_report_preserves_failure_effect_and_source_review(self):
        patient = {'resourceType': 'Patient', 'id': 'patient-1', 'gender': 'female'}
        self.create(self.single, [(1, 1, [patient]), (4, 1, [patient])])
        self.create(self.multi, [(1, 0, []), (3, 1, [dict(patient, address=[{'city': 'Example'}])])])
        dataset = self.root / 'dataset'
        dataset.mkdir()
        (dataset / 'a.txt').write_text('Patient explicitly lives in Example.')
        output = self.root / 'report'
        metadata = analyze(self.single, self.multi, output, dataset, {4: 3}, 1)
        self.assertEqual(metadata['runs'], [1, 3])
        with (output / 'per_run.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        failed = next(r for r in rows if r['run'] == '1' and r['resource_type'] == 'Patient')
        self.assertEqual(float(failed['unpaired_success_effect_resources']), -1)
        self.assertEqual(float(failed['paired_output_effect_resources']), 0)
        examples = [json.loads(line) for line in (output / 'review_examples.jsonl').read_text().splitlines()]
        self.assertTrue(any(e['source_note'] == 'Patient explicitly lives in Example.' for e in examples))
        self.assertTrue(any(e['single_original_run'] == 4 for e in examples))
