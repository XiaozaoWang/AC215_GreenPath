"""Read-only serving catalog. Requests select existing predictions, never run training."""
import hashlib
import json
import os
from pathlib import Path

from api.artifacts import safe_path
from api.dimensions import BUNDLE_DIR, PROVIDER


class UnknownVersionError(ValueError):
    pass


def locations():
    root = Path(os.environ.get('MODEL_ARTIFACTS_ROOT', str(Path(__file__).resolve().parents[2] / '.artifacts')))
    catalog = Path(os.environ.get('MODEL_CATALOG_PATH', str(root / 'catalog/serving.json')))
    candidates = {}
    historical = Path(__file__).resolve().parents[1] / 'fixtures/boston-heldout-en-v2'
    for directory in [historical, BUNDLE_DIR]:
        if (directory / 'manifest.json').is_file():
            manifest = json.loads((directory / 'manifest.json').read_text())
            version = manifest['artifact_version']
            entry = {'id': version, 'directory': directory, 'manifest_sha256': None, 'label': manifest['region_name']}
            if version in candidates and candidates[version]['directory'] != directory:
                a = (candidates[version]['directory'] / 'manifest.json').read_bytes()
                if a != (directory / 'manifest.json').read_bytes():
                    raise ValueError('Different bundles claim the same serving version')
            candidates[version] = entry
    default = json.loads((BUNDLE_DIR / 'manifest.json').read_text())['artifact_version']
    if catalog.exists():
        payload = json.loads(catalog.read_text())
        if payload.get('format_version') != 'greenpath-serving-catalog-v1' or not isinstance(payload.get('versions'), list):
            raise ValueError('Invalid serving catalog')
        seen = set()
        for item in payload['versions']:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
                raise ValueError('Catalog versions require nonempty string IDs')
            version = item['id']
            if version in seen:
                raise ValueError('Duplicate serving version in catalog')
            seen.add(version)
            if not isinstance(item.get('bundle_path'), str) or not isinstance(item.get('label', version), str):
                raise ValueError('Catalog paths and labels must be strings')
            directory = safe_path(root, item['bundle_path'])
            pin = item.get('manifest_sha256')
            if not isinstance(pin, str) or len(pin) != 64 or any(c not in '0123456789abcdef' for c in pin):
                raise ValueError('Catalog entries require a manifest SHA-256')
            if version in candidates and (directory / 'manifest.json').is_file():
                existing = hashlib.sha256((candidates[version]['directory'] / 'manifest.json').read_bytes()).hexdigest()
                if existing != pin:
                    raise ValueError('Catalog conflicts with an existing serving identity')
            candidates[version] = {'id': version, 'directory': directory, 'manifest_sha256': pin,
                                   'label': item.get('label', version), 'run_id': item.get('run_id')}
        default = payload.get('default_version') or default
    if default not in candidates:
        raise ValueError('Catalog default version is not registered')
    return candidates, default


def resolve(version_id=None):
    entries, default = locations()
    selected = version_id or default
    if selected not in entries:
        raise UnknownVersionError(f'Unknown serving version: {selected}')
    entry = entries[selected]
    raw = (entry['directory'] / 'manifest.json').read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if entry['manifest_sha256'] and actual != entry['manifest_sha256']:
        raise ValueError('Registered serving manifest changed; immutable version violated')
    if json.loads(raw).get('artifact_version') != selected:
        raise ValueError('Catalog version and bundle identity differ')
    return entry, actual


def list_versions():
    if PROVIDER == 'legacy':
        return {'default_version': 'legacy-fake', 'versions': [{'id': 'legacy-fake', 'label': 'Legacy simulated square grid',
            'available': True, 'n_cells': 10000, 'mode': 'simulated', 'model_version': 'legacy-fake', 'dataset_version': 'legacy-demo'}]}
    from api.model_bundle import get_bundle
    entries, default = locations()
    versions = []
    for version, entry in entries.items():
        try:
            bundle = get_bundle(version)
            m = bundle.manifest
            provenance = bundle.provenance
            versions.append({'id': version, 'label': entry['label'], 'available': True,
                'model_version': m['model_version'], 'dataset_version': m['dataset_version'],
                'source_version': provenance.get('source_workspace_version') or provenance.get('source_version', m.get('source_version')),
                'snapshot_version': provenance.get('snapshot_version') or provenance.get('source_version'),
                'run_id': provenance.get('run_id') or entry.get('run_id') or m.get('run_id'),
                'mode': provenance.get('mode', 'historical-heldout'), 'n_cells': len(bundle.tiles),
                'prediction_source': m['prediction_source'], 'description': m['description'],
                'evaluation_note': provenance.get('evaluation_note', 'Historical held-out predictions; see experiment provenance.')})
        except (ValueError, FileNotFoundError, KeyError) as error:
            versions.append({'id': version, 'label': entry['label'], 'available': False,
                             'error': str(error)})
    return {'default_version': default, 'versions': versions}
