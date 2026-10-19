"""Resume decisions and cost boundaries without executing Docker or network calls."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_all


class WorkflowChecks(unittest.TestCase):
    def test_unfinished_labels_require_explicit_permission_before_jobs(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / 'configs').mkdir()
            (root / 'configs/source.json').write_text(json.dumps({'source_version': 'source-v1'}))
            config = {'source_config': 'configs/source.json', 'source_directory': '.artifacts/sources/source-v1',
                      'snapshot_version': 'snapshot-v1', 'training_config': 'configs/train.json',
                      'mode': 'full', 'max_new_label_images': 10}
            args = SimpleNamespace(no_app=True, offline=False, allow_labeling=False)
            folder = root / 'workflow'; folder.mkdir()
            with patch.object(run_all, 'ROOT', root), patch.object(run_all, 'ARTIFACTS', root / '.artifacts'), patch.object(run_all, 'execute') as execute:
                with self.assertRaisesRegex(ValueError, 'allow-labeling'):
                    run_all.run_workflow(args, config, {}, {}, folder)
                execute.assert_not_called()

    def test_source_resume_accepts_legitimate_master_table_extension(self):
        with tempfile.TemporaryDirectory() as scratch:
            workspace = Path(scratch)
            (workspace / 'processed').mkdir(); (workspace / 'runs/one').mkdir(parents=True)
            (workspace / 'processed/cells.parquet').write_bytes(b'extended by tiles')
            table = workspace / 'processed/streetview.parquet'; table.write_bytes(b'unchanged images')
            spec = {'example': 'same configuration'}
            record = {'config_sha256': hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
                      'implementation_sha256': run_all.digest(run_all.ROOT / 'src/data-collector/greenpath_source/streetview.py'),
                      'output_sha256': {'processed/cells.parquet': 'old-master-table-hash',
                                        'processed/streetview.parquet': run_all.digest(table)}}
            (workspace / 'runs/one/01_streetview_manifest.json').write_text(json.dumps(record))
            self.assertTrue(run_all.stage_ready(workspace, '01_streetview', spec))
            table.write_bytes(b'changed images')
            self.assertFalse(run_all.stage_ready(workspace, '01_streetview', spec))
