"""Register, serve, import and explicitly publish immutable pipeline artifacts."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from artifact_catalog import ROOT, ARTIFACTS, atomic_json, read_catalog, refresh_runs, register_bundle
from runtime_environment import environment
sys.path.insert(0, str(ROOT / 'src/model-deploy'))
from greenpath_model.artifact import digest, prepared_directory, seal, verify, write_json


def artifact_tool(action, uri, local, env, build=True):
    adc = Path(env.get('ADC_FILE') or str(Path.home() / '.config/gcloud/application_default_credentials.json')).expanduser().resolve()
    if not adc.is_file():
        raise ValueError('Authorized ADC file is required for explicit GCS transfer')
    env = dict(env, ADC_FILE=str(adc))
    local = Path(local).resolve()
    if not local.is_relative_to(ARTIFACTS.resolve()):
        raise ValueError('Transfer directory must be inside .artifacts')
    if build:
        subprocess.run(['docker', 'compose', '-f', 'docker-compose.yml', 'build', 'api-service'], cwd=ROOT, env=env, check=True)
    command = ['docker', 'compose', '-p', 'greenpath-artifacts', '-f', 'docker-compose.yml', '-f', 'docker-compose.gcp.yml',
               '--profile', 'tools', 'run', '--rm', '--no-deps', '--user', env['LOCAL_UID'] + ':' + env['LOCAL_GID'],
               'artifact-tools', action, uri, '/artifacts/' + local.relative_to(ARTIFACTS.resolve()).as_posix()]
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def publish_run(run_id, gcs_root, env, no_build=False, include_upstream=False):
    from run_pipeline import validate_run_id, verify_outputs
    validate_run_id(run_id)
    folder = ARTIFACTS / 'pipeline/runs' / run_id
    summary = json.loads((folder / 'run-summary.json').read_text())
    if not summary.get('success'):
        raise ValueError('Publish a successful reviewed run, not partial or failed outputs')
    verify_outputs(folder)
    if not gcs_root.startswith('gs://') or not gcs_root[5:].strip('/'):
        raise ValueError('Specify a gs://bucket[/prefix] publication root')
    root = gcs_root.rstrip('/')
    records = []
    preparation = json.loads((folder / 'prepared/preparation.json').read_text())
    snapshot = ARTIFACTS / 'snapshots/sources' / preparation['source_version']
    if not snapshot.exists():
        snapshot = ARTIFACTS / 'pipeline/cache/sources' / preparation['source_version']
    artifacts = [('pipeline-inputs', snapshot), ('datasets', folder / 'prepared'),
                 ('features', folder / 'embeddings'), ('models', folder / 'model'),
                 ('predictions', folder / 'predictions'), ('serving', folder / 'serving')]
    if include_upstream:
        source_meta = json.loads((snapshot / 'upstream/source-workspace.json').read_text())
        workspace = ARTIFACTS / 'sources' / source_meta['source_version']
        frozen_sources = json.loads((snapshot / 'source.json').read_text())['source_files']
        for relative, checksum in frozen_sources.items():
            if digest(workspace / relative) != checksum:
                raise ValueError('Source workspace changed after freezing; do not archive it as the old snapshot')
        archive = ARTIFACTS / 'source-archives' / preparation['source_version']
        if not archive.exists():
            if not no_build:
                subprocess.run(['docker', 'build', '-t', 'greenpath-data-processor:ml', '-f', 'src/data-processor/Dockerfile', '.'], cwd=ROOT, env=env, check=True)
            archive.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['docker', 'run', '--rm', '--user', env['LOCAL_UID'] + ':' + env['LOCAL_GID'],
                '--mount', 'type=bind,source=' + str(workspace) + ',target=/source,readonly',
                '--mount', 'type=bind,source=' + str(archive.parent) + ',target=/archives',
                '--mount', 'type=bind,source=' + str(snapshot) + ',target=/snapshot,readonly',
                'greenpath-data-processor:ml', 'archive', '--data-root', '/source', '--output', '/archives/' + archive.name,
                '--artifact-version', preparation['source_version'], '--snapshot-dir', '/snapshot'], cwd=ROOT, env=env, check=True)
        verify(archive, 'greenpath-source-archive-v1')
        if json.loads((archive / 'archive.json').read_text()).get('snapshot_manifest_sha256') != digest(snapshot / 'manifest.json'):
            raise ValueError('Source archive is not bound to this frozen snapshot')
        artifacts.insert(0, ('source-archives', archive))
    # Validate the complete set before issuing uploads. Publications can resume safely.
    manifests = [(kind, path, verify(path)) for kind, path in artifacts]
    records = [{'kind': kind, 'uri': root + '/' + kind + '/' + manifest['artifact_version'],
                'manifest_sha256': digest(path / 'manifest.json')} for kind, path, manifest in manifests]
    release = ARTIFACTS / 'releases' / run_id
    if release.exists():
        verify(release, 'greenpath-release-v1')
        if json.loads((release / 'release.json').read_text())['artifacts'] != records:
            raise ValueError('Release is already sealed with a different publication set or GCS root')
    for index, (kind, path, manifest) in enumerate(manifests):
        uri = root + '/' + kind + '/' + manifest['artifact_version']
        artifact_tool('publish', uri, path, env, build=(not no_build and index == 0))

    if not release.exists():
        with prepared_directory(release) as staging:
            write_json(staging / 'release.json', {'run_id': run_id, 'dataset_version': preparation['dataset_version'],
                'artifacts': records, 'code_revision': summary.get('git_commit'), 'run_summary': summary})
            seal(staging, 'release-' + run_id, 'greenpath-release-v1')
    else:
        verify(release, 'greenpath-release-v1')
        if json.loads((release / 'release.json').read_text())['artifacts'] != records:
            raise ValueError('This release was already sealed with different publications; retain the existing release or select a new run')
    artifact_tool('publish', root + '/releases/release-' + run_id, release, env, build=False)
    atomic_json(ARTIFACTS / 'catalog/publications' / (run_id + '.json'), {'run_id': run_id, 'artifacts': records})
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('refresh')
    commands.add_parser('list')
    register = commands.add_parser('register')
    register.add_argument('directory', type=Path)
    register.add_argument('--label')
    register.add_argument('--default', action='store_true')
    serve = commands.add_parser('serve')
    serve.add_argument('--version')
    serve.add_argument('--api-port', type=int, default=9030)
    serve.add_argument('--frontend-port', type=int, default=3030)
    serve.add_argument('--no-build', action='store_true')
    fetch = commands.add_parser('fetch-serving')
    fetch.add_argument('--uri', required=True)
    fetch.add_argument('--version', required=True)
    fetch.add_argument('--no-build', action='store_true')
    publish = commands.add_parser('publish-run')
    publish.add_argument('--run-id', required=True)
    publish.add_argument('--gcs-root', default='gs://ac215-greenpath')
    publish.add_argument('--include-upstream', action='store_true')
    publish.add_argument('--no-build', action='store_true')
    args = parser.parse_args()
    env = environment(ROOT)
    if args.action == 'refresh':
        print(json.dumps(refresh_runs(), indent=2))
    elif args.action == 'list':
        print(json.dumps(read_catalog(), indent=2))
    elif args.action == 'register':
        print(json.dumps(register_bundle(args.directory, args.label, args.default), indent=2))
    elif args.action == 'serve':
        from run_pipeline import check_ports, validate_run_id
        refresh_runs()
        if args.version and args.version != 'boston-heldout-en-v2':
            rows = {row['id']: row for row in read_catalog()['versions']}
            if args.version not in rows:
                raise ValueError('Version not registered locally; import its serving bundle first')
            register_bundle(ARTIFACTS / rows[args.version]['bundle_path'], make_default=True)
        elif args.version:
            from artifact_catalog import catalog_lock
            with catalog_lock() as path:
                catalog = read_catalog(); catalog['default_version'] = args.version; atomic_json(path, catalog)
        check_ports([args.api_port, args.frontend_port], env)
        env.update({'API_PORT': str(args.api_port), 'FRONTEND_PORT': str(args.frontend_port),
                    'MODEL_PROVIDER': 'bundle', 'MODEL_BUNDLE_DIR': '/app/fixtures/boston-heldout-en-v2', 'LLM_PROVIDER': 'mock'})
        command = ['docker', 'compose', '-p', 'greenpath-pipeline-app', '-f', 'docker-compose.yml', 'up', '-d', '--wait']
        if not args.no_build: command.append('--build')
        subprocess.run(command, cwd=ROOT, env=env, check=True)
    elif args.action == 'fetch-serving':
        if not args.version or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.version):
            raise ValueError('Use a lowercase version ID without path separators')
        target = ARTIFACTS / 'serving' / args.version
        artifact_tool('fetch', args.uri, target, env, build=not args.no_build)
        if verify(target)['artifact_version'] != args.version:
            raise ValueError('Downloaded serving identity differs from requested version')
        print(json.dumps(register_bundle(target), indent=2))
    else:
        print(json.dumps(publish_run(args.run_id, args.gcs_root, env, args.no_build, args.include_upstream), indent=2))


if __name__ == '__main__':
    main()
