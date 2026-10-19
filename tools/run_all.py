"""One explicit acquisition-to-app workflow with resumable sources and immutable outputs."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

from artifact_catalog import ROOT, ARTIFACTS, atomic_json, register_bundle
from runtime_environment import environment
from run_pipeline import execute, validate_run_id, check_ports, verify_outputs, check_api
sys.path.insert(0, str(ROOT / 'src/model-deploy'))
from greenpath_model.artifact import digest, verify


def project_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError('Workflow paths must stay inside this checkout')
    return path


sys.path.insert(0, str(ROOT / 'src/data-source'))
from greenpath_source.workflow import stage_ready as source_stage_ready


def stage_ready(workspace, stage, source_spec):
    """Compatibility entry point for source resume checks."""
    return source_stage_ready(workspace, stage, source_spec,
                              ROOT / 'src/data-source/greenpath_source')


def run_workflow(args, config, env, record, folder):
    source_file = project_path(config['source_config'])
    source_spec = json.loads(source_file.read_text())
    workspace = project_path(config['source_directory'])
    if not workspace.is_relative_to(ARTIFACTS.resolve()):
        raise ValueError('Source working data must live inside .artifacts')
    snapshot_id = config['snapshot_version']
    if not snapshot_id or len(snapshot_id) > 80 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in snapshot_id):
        raise ValueError('Use a safe lowercase snapshot version')
    snapshot = ARTIFACTS / 'snapshots/sources' / snapshot_id
    marker = workspace / 'source-workspace.json'
    if marker.exists() and not snapshot.exists():
        identity = json.loads(marker.read_text())
        expected = hashlib.sha256(json.dumps(source_spec, sort_keys=True).encode()).hexdigest()
        if identity['source_version'] != source_spec['source_version'] or identity['config_sha256'] != expected:
            raise ValueError('Source workspace identity/configuration differs from the workflow; use a new directory/version')
    selection = project_path(config['training_config'])
    if config['mode'] not in ['smoke', 'full']:
        raise ValueError('Workflow mode must be smoke or full')
    limit = config['max_new_label_images']
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError('Specify a positive finite per-invocation new-label image limit')
    if not args.no_app:
        check_ports([args.api_port, args.frontend_port], env)
    logs = folder / 'logs'; logs.mkdir(exist_ok=True)
    env = dict(env, SOURCE_CONFIG=str(source_file), SOURCE_DATA_DIR=str(workspace))
    source_base = ['docker', 'compose', '-p', 'greenpath-sources', '-f', 'docker-compose.sources.yml']
    if snapshot.exists():
        manifest = verify(snapshot, 'greenpath-processed-snapshot-v1')
        frozen = json.loads((snapshot / 'upstream/source-workspace.json').read_text())
        expected = hashlib.sha256(json.dumps(source_spec, sort_keys=True).encode()).hexdigest()
        if frozen['config_sha256'] != expected or manifest['artifact_version'] != snapshot_id or digest(snapshot / 'selected-config.json') != digest(selection):
            raise ValueError('Frozen snapshot belongs to other inputs/configuration; choose a new snapshot version')
        current_prompt = ROOT / 'prompts' / (source_spec['labeling']['prompt_version'] + '.txt')
        if json.loads((snapshot / 'processed/labels_meta.json').read_text())['prompt_sha'] != digest(current_prompt):
            raise ValueError('Prompt content changed since this snapshot; choose a new source/snapshot version for relabeling')
        record['source_status'] = 'reused_immutable_snapshot'
    else:
        if args.offline:
            raise ValueError('Offline run-all requires an already frozen local snapshot')
        meta_path = workspace / 'processed/labels_meta.json'
        complete = meta_path.exists() and json.loads(meta_path.read_text()).get('complete') is True
        if not complete and not args.allow_labeling:
            raise ValueError('Upstream labeling is incomplete. Add --allow-labeling explicitly to permit up to the configured new-image limit, or use an existing snapshot.')
        workspace.mkdir(parents=True, exist_ok=True)
        # One Compose run creates one container for all four upstream stages.
        allowance = limit if args.allow_labeling else 0
        command = list(source_base)
        if allowance:
            adc = Path(env.get('ADC_FILE') or str(Path.home() / '.config/gcloud/application_default_credentials.json')).expanduser()
            if not adc.is_file() or not env.get('GOOGLE_CLOUD_PROJECT'):
                raise ValueError('Paid labeling requires ADC and GOOGLE_CLOUD_PROJECT in .env')
            env['ADC_FILE'] = str(adc.resolve())
            command += ['-f', 'docker-compose.sources.gcp.yml']
        command += ['run', '--rm']
        if not args.no_build: command += ['--build']
        command += ['data-source', 'all', '--config', '/config/source.json',
                    '--data-root', '/data', '--max-images', str(allowance)]
        if args.allow_labeling:
            command += ['--allow-labeling']
        execute(command, env, logs / '01-04_sources.log')
        source_summary = json.loads((workspace / 'source-run-summary.json').read_text())
        if not source_summary.get('success'):
            raise ValueError('Combined source job did not report success')
        record['stages'] = source_summary['stages']
        atomic_json(folder / 'workflow-summary.json', record)
        labels = json.loads(meta_path.read_text())
        if not labels.get('complete') or labels['images_pending']:
            raise ValueError('Label limit reached with %s images pending; outputs are saved. Resume explicitly to label another bounded batch.' % labels['images_pending'])
        record['stages']['04_labeling'] = 'complete_selected_workspace'
        if not args.no_build:
            execute(['docker', 'build', '-t', 'greenpath-data-processor:ml', '-f', 'src/data-processor/Dockerfile', '.'], env, logs / 'snapshot-build.log')
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        execute(['docker', 'run', '--rm', '--user', env['LOCAL_UID'] + ':' + env['LOCAL_GID'],
                 '--mount', 'type=bind,source=' + str(workspace) + ',target=/source,readonly',
                 '--mount', 'type=bind,source=' + str(selection) + ',target=/selection.json,readonly',
                 '--mount', 'type=bind,source=' + str(snapshot.parent) + ',target=/snapshots',
                 'greenpath-data-processor:ml', 'snapshot', '--data-root', '/source', '--config', '/selection.json',
                 '--output', '/snapshots/' + snapshot_id, '--artifact-version', snapshot_id], env, logs / 'freeze.log')
        verify(snapshot, 'greenpath-processed-snapshot-v1')
        record['source_status'] = 'collected_labeled_and_frozen'
    record.update({'source_version': source_spec['source_version'], 'snapshot_version': snapshot_id,
                   'snapshot_manifest_sha256': digest(snapshot / 'manifest.json')})
    atomic_json(folder / 'workflow-summary.json', record)
    previous = record.get('training_run_id')
    if previous:
        old = ARTIFACTS / 'pipeline/runs' / previous / 'run-summary.json'
        if old.is_file() and json.loads(old.read_text()).get('success'):
            verified = verify_outputs(old.parent)
            register_bundle(old.parent / 'serving', config['result_label'], make_default=True, run_id=previous)
            if not args.no_app and not json.loads(old.read_text()).get('app', {}).get('verified'):
                command = [sys.executable, str(ROOT / 'tools/manage_artifacts.py'), 'serve', '--version', verified['serving_version'],
                           '--api-port', str(args.api_port), '--frontend-port', str(args.frontend_port)]
                if args.no_build: command.append('--no-build')
                execute(command, env, logs / 'resume-app.log')
                check_api(args.api_port, old.parent, verified)
                record['app_resumed'] = True
            record['serving_version'] = verified['serving_version']
            record['success'] = True
            return
    attempt = 1
    training_id = args.run_id
    while (ARTIFACTS / 'pipeline/runs' / training_id).exists():
        attempt += 1
        training_id = args.run_id + '-retry-' + str(attempt)
    validate_run_id(training_id)
    record['training_run_id'] = training_id
    atomic_json(folder / 'workflow-summary.json', record)
    command = [sys.executable, str(ROOT / 'tools/run_pipeline.py'), '--mode', config['mode'], '--run-id', training_id,
               '--source-dir', str(snapshot), '--catalog-label', config['result_label'],
               '--api-port', str(args.api_port), '--frontend-port', str(args.frontend_port)]
    if args.no_app: command += ['--no-app']
    if args.no_build: command += ['--no-build']
    if args.offline: command += ['--offline']
    execute(command, env, logs / ('training-' + training_id + '.log'))
    serving = ARTIFACTS / 'pipeline/runs' / training_id / 'serving'
    register_bundle(serving, config['result_label'], make_default=True, run_id=training_id)
    record['serving_version'] = verify(serving)['artifact_version']
    record['success'] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/workflow-boston-small.json')
    parser.add_argument('--run-id')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--allow-labeling', action='store_true', help='Explicitly permit a bounded batch of paid teacher requests')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--no-app', action='store_true')
    parser.add_argument('--no-build', action='store_true')
    parser.add_argument('--api-port', type=int, default=9000)
    parser.add_argument('--frontend-port', type=int, default=3000)
    args = parser.parse_args()
    args.run_id = validate_run_id(args.run_id or ('workflow-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]))
    if len(args.run_id) > 40:
        raise ValueError('Workflow IDs must leave room for retry suffixes: maximum 40 characters')
    config = json.loads(args.config.read_text())
    allowed = {'format_version', 'source_config', 'source_directory', 'snapshot_version', 'training_config', 'mode', 'max_new_label_images', 'result_label', 'scope_note'}
    if set(config) != allowed:
        raise ValueError('Use the documented workflow configuration keys; credentials belong in .env')
    if config.get('format_version') != 'greenpath-workflow-v1':
        raise ValueError('Unsupported workflow configuration')
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    folder = ARTIFACTS / 'workflows' / args.run_id
    if folder.exists() and not args.resume:
        raise ValueError('Workflow already exists; use --resume or select a new run ID')
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        summary = folder / 'workflow-summary.json'
        record = json.loads(summary.read_text()) if summary.exists() else {'workflow_id': args.run_id, 'config_sha256': fingerprint, 'configuration': config, 'started_at': datetime.now(timezone.utc).isoformat()}
        if record['config_sha256'] != fingerprint:
            raise ValueError('Workflow configuration changed; choose a new workflow ID')
        record['implementation_sha256'] = digest(__file__)
        record['git_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        record['success'] = False
        record.pop('error', None)
        try:
            run_workflow(args, config, environment(ROOT), record, folder)
        except Exception as error:
            record['error'] = str(error)
            raise
        finally:
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
            atomic_json(summary, record)
            print(json.dumps(record, indent=2), flush=True)


if __name__ == '__main__':
    main()
