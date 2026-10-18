"""Standard-library local catalog for immutable application results."""
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import sys
import tempfile
import os

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / '.artifacts'
sys.path.insert(0, str(ROOT / 'src/model-deploy'))
from greenpath_model.artifact import digest, verify


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write('\n')
        os.chmod(name, 0o644)  # The non-root API reads catalogs through a read-only host mount.
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def catalog_lock():
    directory = ARTIFACTS / 'catalog'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield directory / 'serving.json'


def read_catalog():
    path = ARTIFACTS / 'catalog/serving.json'
    if not path.exists():
        return {'format_version': 'greenpath-serving-catalog-v1', 'default_version': 'boston-heldout-en-v2', 'versions': []}
    data = json.loads(path.read_text())
    if data.get('format_version') != 'greenpath-serving-catalog-v1' or not isinstance(data.get('versions'), list):
        raise ValueError('Unsupported serving catalog')
    return data


def register_bundle(directory, label=None, make_default=False, run_id=None):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ARTIFACTS.resolve()):
        raise ValueError('Catalog bundles must be inside .artifacts')
    manifest = verify(directory, 'greenpath-region-v1')
    contract = json.loads((ROOT / 'api-service/api/dimensions.json').read_text())
    if json.loads((directory / manifest['schema_file']).read_text()) != contract:
        raise ValueError('This app catalog supports one matching dimension contract; incompatible bundles require a matching API deployment')
    version = manifest['artifact_version']
    if not isinstance(version, str) or not version:
        raise ValueError('Serving versions must be nonempty strings')
    checksum = digest(directory / 'manifest.json')
    with catalog_lock() as path:
        catalog = read_catalog()
        entries = {row['id']: row for row in catalog['versions']}
        if version in entries and entries[version]['manifest_sha256'] != checksum:
            raise ValueError('Serving identity already refers to different contents')
        entry = {'id': version, 'label': label or entries.get(version, {}).get('label') or manifest['region_name'],
                 'bundle_path': directory.relative_to(ARTIFACTS.resolve()).as_posix(),
                 'manifest_sha256': checksum, 'run_id': run_id or entries.get(version, {}).get('run_id')}
        entries[version] = entry
        catalog['versions'] = list(entries.values())
        if make_default:
            catalog['default_version'] = version
        atomic_json(path, catalog)
    return entry


def refresh_runs():
    registered = []
    for summary in sorted((ARTIFACTS / 'pipeline/runs').glob('*/run-summary.json')):
        record = json.loads(summary.read_text())
        if record.get('success'):
            registered.append(register_bundle(summary.parent / 'serving', run_id=summary.parent.name))
    return registered
