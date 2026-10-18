import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import tempfile

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import artifact_catalog as catalog
from runtime_environment import environment


class CatalogChecks(unittest.TestCase):
    def test_same_serving_id_cannot_be_reassigned(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            artifacts = root / '.artifacts'; bundle = artifacts / 'results/example'
            bundle.parent.mkdir(parents=True)
            original = TOOLS.parent / 'api-service/fixtures/boston-heldout-en-v2'
            shutil.copytree(original, bundle)
            (root / 'api-service/api').mkdir(parents=True)
            shutil.copyfile(TOOLS.parent / 'api-service/api/dimensions.json', root / 'api-service/api/dimensions.json')
            with patch.object(catalog, 'ROOT', root), patch.object(catalog, 'ARTIFACTS', artifacts):
                first = catalog.register_bundle(bundle)
                self.assertEqual(first['id'], catalog.register_bundle(bundle)['id'])
                path = bundle / 'manifest.json'; manifest = json.loads(path.read_text())
                manifest['model_version'] = 'different-model'; path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'different contents'):
                    catalog.register_bundle(bundle)

    def test_shell_environment_overrides_dotenv_without_execution(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / '.env').write_text('SOURCE_VERSION=from-file\nADC_FILE="${HOME}/adc.json"\nLITERAL="$(not-a-command)"\n')
            with patch.dict('os.environ', {'HOME': '/example', 'SOURCE_VERSION': 'from-shell'}, clear=True):
                values = environment(root)
                self.assertEqual(values['SOURCE_VERSION'], 'from-shell')
                self.assertEqual(values['ADC_FILE'], '/example/adc.json')
                self.assertEqual(values['LITERAL'], '$(not-a-command)')
