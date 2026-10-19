"""Storage integrity checks using local fake stores; no cloud/model calls."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'src/api-service'))
from api.artifacts import fetch, publish
from cloud_layout import inventory, cloud_root


class MemoryStore:
    def __init__(self):
        self.objects = {}

    def get(self, name):
        return self.objects.get(name)

    def create(self, name, payload):
        if name in self.objects and self.objects[name] != payload:
            raise ValueError('Immutable object conflict')
        self.objects[name] = payload


class MirroredStorageChecks(unittest.TestCase):
    def test_run_inventory_includes_nested_stage_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'serving').mkdir()
            (root / 'serving/manifest.json').write_text('{"stage": "identity"}')
            (root / '.DS_Store').write_text('machine-specific')
            manifest = inventory(root, 'run-example', 'greenpath-run-v1')
            self.assertIn('serving/manifest.json', manifest['files'])
            self.assertNotIn('.DS_Store', manifest['files'])

    def test_full_restore_completes_a_serving_only_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / 'remote', root / 'local'
            (source / 'serving').mkdir(parents=True)
            (source / 'serving/scores.json').write_text('saved predictions')
            (source / 'model').mkdir()
            (source / 'model/weights.npz').write_bytes(b'saved model fixture')
            inventory(source, 'run-example', 'greenpath-run-v1')
            store = MemoryStore()
            publish(source, store)
            (destination / 'serving').mkdir(parents=True)
            (destination / 'serving/scores.json').write_text('saved predictions')
            fetch(destination, store, merge=True)
            self.assertEqual((destination / 'model/weights.npz').read_bytes(), b'saved model fixture')
            self.assertTrue((destination / 'manifest.json').is_file())

    def test_restore_refuses_conflicts_before_writing_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / 'remote', root / 'local'
            source.mkdir(); destination.mkdir()
            (source / 'a.txt').write_text('missing local file')
            (source / 'z.txt').write_text('remote contents')
            (destination / 'z.txt').write_text('keep local contents')
            inventory(source, 'run-example', 'greenpath-run-v1')
            store = MemoryStore(); publish(source, store)
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                fetch(destination, store, merge=True)
            self.assertEqual((destination / 'z.txt').read_text(), 'keep local contents')
            self.assertFalse((destination / 'a.txt').exists())

    def test_traversal_in_publication_root_is_refused(self):
        with self.assertRaises(ValueError):
            cloud_root('gs://bucket/artifacts/../outside')
