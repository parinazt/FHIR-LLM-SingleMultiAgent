"""Package paths and experiment identity checks."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.agent import PROMPT_DIR
from app.config import ROOT, Settings
from app.experiment_manifest import build_manifest, check_resume_manifest
from app.fhir_validator import FHIRValidator
from app.results_paths import result_directory


class AppLayoutTests(unittest.TestCase):
    def test_external_data_paths_still_point_at_project_root(self):
        self.assertEqual(PROMPT_DIR, ROOT / 'app' / 'agents')
        self.assertEqual(result_directory('multiagent', 'example'), ROOT / 'results' / 'multi' / 'example')
        self.assertEqual(FHIRValidator().jar_path, ROOT / 'validator' / 'validator_cli.jar')

    def test_matching_manifest_accepted_and_changed_extraction_rejected(self):
        llm = SimpleNamespace(configuration=lambda: {'model': 'fake'})
        current = build_manifest(Settings(num_of_execution=1), llm, [(Path('a.txt'), 'note')])
        previous = copy.deepcopy(current)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'experiment.json'
            path.write_text(json.dumps(previous))
            check_resume_manifest(path, current)
            previous['identity']['implementation']['agent.py'] = 'unrelated-change'
            path.write_text(json.dumps(previous))
            with self.assertRaisesRegex(ValueError, 'differ'):
                check_resume_manifest(path, current, allow_extension=True)
