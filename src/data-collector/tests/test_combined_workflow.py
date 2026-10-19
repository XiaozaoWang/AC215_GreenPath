"""Combined upstream coordination without network calls or real stage execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from greenpath_source import workflow


class CombinedWorkflowChecks(unittest.TestCase):
    def test_no_paid_permission_stops_before_any_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            options = {'root': Path(directory), 'spec': {'source_version': 'test-source'}, 'max_images': 0}
            with patch.object(workflow, 'run_stage') as run:
                with self.assertRaisesRegex(ValueError, 'allow-labeling'):
                    workflow.run_all(options)
                run.assert_not_called()

    def test_reuses_ready_stages_and_passes_one_bounded_label_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'processed').mkdir()
            (root / 'processed/labels_meta.json').write_text(json.dumps({'complete': True, 'images_pending': 0}))
            options = {'root': root, 'spec': {'source_version': 'test-source'}, 'max_images': 5}
            with patch.object(workflow, 'stage_ready', return_value=True), patch.object(workflow, 'run_stage') as run:
                result = workflow.run_all(options, allow_labeling=True)
            run.assert_called_once_with('labeling', options)
            self.assertTrue(result['success'])
            self.assertEqual(result['stages']['01_streetview'], 'reused_verified_outputs')

    def test_missing_stages_execute_in_order_before_labeling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'processed').mkdir()
            (root / 'processed/labels_meta.json').write_text(json.dumps({'complete': True, 'images_pending': 0}))
            options = {'root': root, 'spec': {'source_version': 'test-source'}, 'max_images': 5}
            with patch.object(workflow, 'stage_ready', side_effect=[False, True, False, True, False, True]), patch.object(workflow, 'run_stage') as run:
                workflow.run_all(options, allow_labeling=True)
            self.assertEqual([call.args[0] for call in run.call_args_list],
                             ['streetview', 'tiles', 'features', 'labeling'])

    def test_partial_labels_do_not_report_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'processed').mkdir()
            (root / 'processed/labels_meta.json').write_text(json.dumps({'complete': False, 'images_pending': 3}))
            options = {'root': root, 'spec': {'source_version': 'test-source'}, 'max_images': 2}
            with patch.object(workflow, 'stage_ready', return_value=True), patch.object(workflow, 'run_stage') as run:
                with self.assertRaisesRegex(ValueError, '3 images pending'):
                    workflow.run_all(options, allow_labeling=True)
            run.assert_called_once_with('labeling', options)
