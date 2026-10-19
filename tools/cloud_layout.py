"""Mirrored publication/restore paths. Cache and catalog remain machine-local."""
from contextlib import contextmanager
import fcntl
import json
import re
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit

from artifact_catalog import ROOT, ARTIFACTS, atomic_json, register_bundle
from greenpath_model.artifact import digest, verify, safe_path, write_json, prepared_directory

DEFAULT_ROOT = 'gs://ac215-greenpath/artifacts'
LAYOUT = 'greenpath-mirrored-layout-v1'


def cloud_root(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'gs' or not parsed.netloc or parsed.query or parsed.fragment or '..' in parsed.path.split('/'):
        raise ValueError('Expected gs://bucket[/prefix]')
    return value.rstrip('/')


def run_id(value):
    from run_pipeline import validate_run_id
    return validate_run_id(value)


def version_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', value):
        raise ValueError('Unsafe artifact version/path component')
    return value


def inventory(root, version, format_version, **metadata):
    root = Path(root)
    files = {}
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root)
        if p.is_symlink():
            raise ValueError('Symlink in publication: ' + str(rel))
        if p.is_file() and not any(part.startswith('.') for part in rel.parts) and rel.as_posix() != 'manifest.json':
            files[rel.as_posix()] = {'sha256': digest(p), 'size_bytes': p.stat().st_size}
    if not files:
        raise ValueError('Cannot publish an empty artifact')
    # Include nested stage manifests: only this package's root manifest is excluded.
    write_json(root / 'manifest.json', dict(artifact_version=version, format_version=format_version, files=files, **metadata))
    return verify(root)


@contextmanager
def package_tree(source, version, format_version, **metadata):
    ARTIFACTS.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.publication-', dir=ARTIFACTS) as directory:
        target = Path(directory) / 'content'
        shutil.copytree(source, target, symlinks=True,
                        ignore=lambda folder, names: [n for n in names if n.startswith('.')])
        inventory(target, version, format_version, **metadata)
        yield target


def record(kind, local, relative, root):
    m = verify(local)
    return {'kind': kind, 'relative_path': relative, 'uri': root + '/' + relative,
            'artifact_version': m['artifact_version'], 'manifest_sha256': digest(local / 'manifest.json')}


def publish_run(identifier, root, env, transfer, include_upstream=False, no_build=False):
    from run_pipeline import verify_outputs
    identifier = run_id(identifier)
    root = cloud_root(root)
    folder = ARTIFACTS / 'pipeline/runs' / identifier
    summary = json.loads((folder / 'run-summary.json').read_text())
    if not summary.get('success'):
        raise ValueError('Publish only a successful reviewed run')
    verify_outputs(folder)
    preparation = json.loads((folder / 'prepared/preparation.json').read_text())
    snapshot_id = preparation.get('snapshot_version') or preparation['source_version']
    version_id(snapshot_id)
    snapshot = ARTIFACTS / 'snapshots/sources' / snapshot_id
    if not snapshot.exists():
        snapshot = ARTIFACTS / 'pipeline/cache/sources' / snapshot_id
    verify(snapshot, 'greenpath-processed-snapshot-v1')
    model = json.loads((folder / 'model/model.json').read_text())
    encoder_version = model['encoder']['artifact_version'] if 'artifact_version' in model['encoder'] else model['encoder']['version']
    version_id(encoder_version)
    encoder = ARTIFACTS / 'pipeline/cache/encoders' / encoder_version
    verify(encoder, 'greenpath-clip-encoder-v1')
    if digest(encoder / 'manifest.json') != model['encoder']['manifest_sha256']:
        raise ValueError('Encoder differs from the model reference')
    records = []
    built = False

    def send(kind, local, relative):
        nonlocal built
        item = record(kind, local, relative, root)
        transfer('publish', item['uri'], local, env, build=not no_build and not built)
        built = True
        records.append(item)

    if include_upstream:
        origin = json.loads((snapshot / 'upstream/source-workspace.json').read_text())
        source_version = origin['source_version']
        version_id(source_version)
        workspace = ARTIFACTS / 'sources' / source_version
        # Lock the actual workspace, not a second lock inside the temporary package.
        with (workspace / '.source.lock').open('r') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for name, checksum in json.loads((snapshot / 'source.json').read_text())['source_files'].items():
                if digest(safe_path(workspace, name)) != checksum:
                    raise ValueError('Workspace inputs changed since freezing; use a new source/snapshot version')
            with package_tree(workspace, source_version, 'greenpath-source-workspace-v1',
                              snapshot_manifest_sha256=digest(snapshot / 'manifest.json')) as package:
                send('source', package, 'sources/' + source_version)
    send('snapshot', snapshot, 'snapshots/sources/' + snapshot_id)
    send('encoder', encoder, 'encoders/' + encoder_version)
    with package_tree(folder, 'run-' + identifier, 'greenpath-run-v1', run_id=identifier) as package:
        send('run', package, 'pipeline/runs/' + identifier)
    # Publish immutable stage manifests as completion markers for serving-only downloads.
    # Root package already includes their bytes; this only verifies/reuses identical objects.
    for stage in ['prepared', 'embeddings', 'model', 'predictions', 'serving']:
        transfer('publish', root + '/pipeline/runs/' + identifier + '/' + stage, folder / stage, env, build=False)
    release = ARTIFACTS / 'releases/mirrored-v1' / identifier
    payload = {'layout': LAYOUT, 'run_id': identifier, 'dataset_version': preparation['dataset_version'],
               'artifacts': records, 'serving_version': summary['ml']['serving_version'],
               'source_snapshot_version': snapshot_id}
    if release.exists():
        verify(release)
        if json.loads((release / 'release.json').read_text()) != payload:
            raise ValueError('Mirrored release already exists with different contents/root/options')
    else:
        with prepared_directory(release) as staging:
            write_json(staging / 'release.json', payload)
            inventory(staging, 'release-' + identifier + '-mirrored-v1', 'greenpath-mirrored-release-v1')
    transfer('publish', root + '/releases/mirrored-v1/' + identifier, release, env, build=False)
    receipt = dict(payload, release_uri=root + '/releases/mirrored-v1/' + identifier,
                   publication_root=root, serving_uri=root + '/pipeline/runs/' + identifier + '/serving')
    atomic_json(ARTIFACTS / 'catalog/publications/mirrored-v1' / (identifier + '.json'), receipt)
    return receipt


def fetch_serving(root, identifier, version, env, transfer, no_build=False):
    identifier = run_id(identifier)
    target = ARTIFACTS / 'pipeline/runs' / identifier / 'serving'
    transfer('fetch', cloud_root(root) + '/pipeline/runs/' + identifier + '/serving', target, env, build=not no_build)
    if verify(target, 'greenpath-region-v1')['artifact_version'] != version:
        raise ValueError('Serving identity differs from --version')
    provenance = json.loads((target / 'provenance.json').read_text())
    if provenance.get('run_id') and provenance['run_id'] != identifier:
        raise ValueError('Serving provenance belongs to another run')
    return register_bundle(target, run_id=identifier)


def fetch_run(root, identifier, env, transfer, include_upstream=False, no_build=False):
    identifier = run_id(identifier)
    root = cloud_root(root)
    release = ARTIFACTS / 'releases/mirrored-v1' / identifier
    transfer('fetch', root + '/releases/mirrored-v1/' + identifier, release, env, build=not no_build)
    meta = json.loads((release / 'release.json').read_text())
    if meta.get('layout') != LAYOUT or meta.get('run_id') != identifier:
        raise ValueError('Release format/run identity differs')
    allowed = {'run': 'pipeline/runs/', 'snapshot': 'snapshots/sources/', 'source': 'sources/', 'encoder': 'encoders/'}
    seen = set()
    for item in meta['artifacts']:
        kind, relative = item['kind'], item['relative_path']
        if kind not in allowed or kind in seen or not relative.startswith(allowed[kind]):
            raise ValueError('Unsupported or duplicate release artifact')
        seen.add(kind)
        suffix = relative[len(allowed[kind]):]
        version_id(suffix)
        if kind == 'run' and suffix != identifier:
            raise ValueError('Release points to another run')
        if item['uri'] != root + '/' + relative:
            raise ValueError('Release URI escapes the requested publication root')
        if kind == 'source' and not include_upstream:
            continue
        # Encoders are authoritative cloud artifacts; their local copies are a shared cache.
        destination = ARTIFACTS / ('pipeline/cache/encoders/' + suffix if kind == 'encoder' else relative)
        transfer('restore' if kind in ['run', 'source'] else 'fetch', item['uri'], destination, env, build=False)
        if digest(destination / 'manifest.json') != item['manifest_sha256'] or verify(destination)['artifact_version'] != item['artifact_version']:
            raise ValueError('Restored artifact does not match release identity')
    if include_upstream and 'source' not in seen:
        raise ValueError('This release has no full source workspace; fetch without --include-upstream')
    if not {'run', 'snapshot', 'encoder'} <= seen:
        raise ValueError('Release is missing required artifacts')
    from run_pipeline import verify_outputs
    folder = ARTIFACTS / 'pipeline/runs' / identifier
    verify_outputs(folder)
    result = register_bundle(folder / 'serving', run_id=identifier)
    # A downloaded workspace gets a new local lock, never a copied remote lock.
    if include_upstream:
        for item in meta['artifacts']:
            if item['kind'] == 'source':
                (ARTIFACTS / item['relative_path'] / '.source.lock').touch(exist_ok=True)
    return result
