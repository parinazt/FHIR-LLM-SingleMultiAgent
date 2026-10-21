import csv
import tempfile
import unittest
from pathlib import Path

from compare_csv_metrics import AGENTS, read_runs, summarize


class CSVMetricTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'results.csv'

    def write(self, rows):
        with self.path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['file', 'run-id', 'fhir-valid', 'attempts',
                                                        *(f'{a}-attempts' for a in AGENTS)])
            writer.writeheader()
            writer.writerows(rows)

    def test_equal_run_weight_not_pooled_cases(self):
        self.write([{'file': 'a', 'run-id': 0, 'fhir-valid': 1, 'attempts': 3},
                    {'file': 'a', 'run-id': 1, 'fhir-valid': 0, 'attempts': 1},
                    {'file': 'b', 'run-id': 1, 'fhir-valid': 0, 'attempts': 1}])
        summary = {r['metric']: r for r in summarize(read_runs(self.path, 'single'))}
        self.assertEqual(summary['fhir-valid']['mean'], .5)
        self.assertAlmostEqual(summary['fhir-valid']['sd'], 2 ** -.5)
        self.assertEqual(summary['attempts']['mean'], 2)
        self.assertAlmostEqual(summary['attempts']['sd'], 2 ** .5)

    def test_agent_zero_is_included_blank_is_missing(self):
        self.write([{'file': 'a', 'run-id': 2, 'fhir-valid': 0, 'attempts': '', 'merge-attempts': 0},
                    {'file': 'b', 'run-id': 2, 'fhir-valid': 1, 'attempts': 2, 'merge-attempts': 2}])
        rows = {r['metric']: r for r in read_runs(self.path, 'multi')}
        self.assertEqual(rows['merge-attempts']['run_mean'], 1)
        self.assertEqual(rows['attempts']['run_mean'], 2)
        self.assertEqual(rows['attempts']['n_missing'], 1)
        self.assertIsNone(summarize(list(rows.values()))[0]['sd'])

    def test_duplicate_outcomes_are_rejected(self):
        row = {'file': 'a', 'run-id': 1, 'fhir-valid': 0, 'attempts': 1}
        self.write([row, dict(row, **{'fhir-valid': 1})])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            read_runs(self.path, 'single')

    def test_selected_runs_and_invalid_values(self):
        self.write([{'file': 'a', 'run-id': 4, 'fhir-valid': 1, 'attempts': 1}])
        with self.assertRaisesRegex(ValueError, 'missing requested runs'):
            read_runs(self.path, 'single', [1])
        self.write([{'file': 'a', 'run-id': 1, 'fhir-valid': 2, 'attempts': 1}])
        with self.assertRaisesRegex(ValueError, 'invalid value'):
            read_runs(self.path, 'single')
