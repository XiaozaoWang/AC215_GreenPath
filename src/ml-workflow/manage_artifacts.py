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
    if env.get('GREENPATH_ARTIFACT_TRANSFER') == 'host':
        sys.path.insert(0, str(ROOT / 'src/api-service'))
        from api.artifacts import GCS, publish, fetch
        import os
        previous = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(adc)
        try:
            store = GCS(uri)
            if action == 'publish':
                publish(local, store)
            else:
                fetch(local, store, merge=action == 'restore')
        finally:
            if previous is None:
                os.environ.pop('GOOGLE_APPLICATION_CREDENTIALS', None)
            else:
                os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = previous
        return
    if build:
        subprocess.run(['docker', 'compose', '-f', 'docker-compose.yml', 'build', 'api-service'], cwd=ROOT, env=env, check=True)
    command = ['docker', 'compose', '-p', 'greenpath-artifacts', '-f', 'docker-compose.yml', '-f', 'docker-compose.gcp.yml',
               '--profile', 'tools', 'run', '--rm', '--no-deps', '--user', env['LOCAL_UID'] + ':' + env['LOCAL_GID'],
               'artifact-tools', action, uri, '/artifacts/' + local.relative_to(ARTIFACTS.resolve()).as_posix()]
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def publish_run(run_id, gcs_root, env, no_build=False, include_upstream=False):
    from cloud_layout import publish_run as publish_mirrored
    return publish_mirrored(run_id, gcs_root, env, artifact_tool,
                            include_upstream=include_upstream, no_build=no_build)


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
    serve.add_argument('--api-port', type=int, default=9000)
    serve.add_argument('--frontend-port', type=int, default=3000)
    serve.add_argument('--no-build', action='store_true')
    fetch = commands.add_parser('fetch-serving')
    fetch.add_argument('--uri', help='Explicit URI; legacy flat serving URIs remain supported')
    fetch.add_argument('--run-id', help='Download into pipeline/runs/<run-id>/serving')
    fetch.add_argument('--gcs-root', default='gs://ac215-greenpath/artifacts')
    fetch.add_argument('--version', required=True)
    fetch.add_argument('--no-build', action='store_true')
    restore = commands.add_parser('fetch-run')
    restore.add_argument('--run-id', required=True)
    restore.add_argument('--gcs-root', default='gs://ac215-greenpath/artifacts')
    restore.add_argument('--include-upstream', action='store_true')
    restore.add_argument('--no-build', action='store_true')
    publish = commands.add_parser('publish-run')
    publish.add_argument('--run-id', required=True)
    publish.add_argument('--gcs-root', default='gs://ac215-greenpath/artifacts')
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
        print(f'Frontend: http://localhost:{args.frontend_port}')
        print(f'API docs: http://localhost:{args.api_port}/docs')
    elif args.action == 'fetch-serving':
        from cloud_layout import fetch_serving, cloud_root
        if not args.version or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.version):
            raise ValueError('Use a lowercase version ID without path separators')
        if args.run_id and not args.uri:
            entry = fetch_serving(args.gcs_root, args.run_id, args.version, env, artifact_tool, args.no_build)
        else:
            if not args.uri:
                raise ValueError('Specify --run-id for mirrored storage or --uri for an explicit bundle')
            # New mirrored URIs automatically restore their matching relative run path.
            import re
            match = re.fullmatch(r'(gs://[^?#]+)/pipeline/runs/([a-z0-9][a-z0-9-]*)/serving/?', args.uri)
            if match:
                if args.run_id and args.run_id != match.group(2):
                    raise ValueError('--run-id conflicts with the URI')
                entry = fetch_serving(match.group(1), match.group(2), args.version, env, artifact_tool, args.no_build)
            else:
                if args.run_id:
                    raise ValueError('Use --run-id without a legacy flat URI')
                target = ARTIFACTS / 'serving' / args.version
                artifact_tool('fetch', args.uri, target, env, build=not args.no_build)
                if verify(target)['artifact_version'] != args.version:
                    raise ValueError('Downloaded serving identity differs from requested version')
                entry = register_bundle(target)
        print(json.dumps(entry, indent=2))
    elif args.action == 'fetch-run':
        from cloud_layout import fetch_run
        print(json.dumps(fetch_run(args.gcs_root, args.run_id, env, artifact_tool,
                                   include_upstream=args.include_upstream, no_build=args.no_build), indent=2))
    else:
        print(json.dumps(publish_run(args.run_id, args.gcs_root, env, args.no_build, args.include_upstream), indent=2))


if __name__ == '__main__':
    main()
