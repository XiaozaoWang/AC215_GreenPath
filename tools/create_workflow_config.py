"""Create paired source/workflow configs for a fresh collection; never run jobs."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', help='New version/workflow name, e.g. boston-pilot-v2')
    parser.add_argument('--sample-size', type=int, default=50,
                        help='Maximum eligible cells; 0 selects all (default: 50)')
    parser.add_argument('--label-limit', type=int, default=200,
                        help='New labeled images per invocation, excluding retries (default: 200)')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', args.name):
        parser.error('Name must use 1-40 lowercase letters, digits or hyphens')
    if args.sample_size < 0 or args.label_limit < 1:
        parser.error('--sample-size must be nonnegative and --label-limit must be positive')

    name = args.name
    source_file = ROOT / 'configs' / ('source-' + name + '.json')
    workflow_file = ROOT / 'configs' / ('workflow-' + name + '.json')
    workspace = ROOT / '.artifacts/sources' / name
    snapshot_version = name + '-frozen-v1'
    snapshot = ROOT / '.artifacts/snapshots/sources' / snapshot_version
    protected = [source_file, workflow_file, workspace, snapshot,
                 ROOT / '.artifacts/workflows' / name,
                 ROOT / '.artifacts/pipeline/runs' / name]
    for path in protected:
        if path.exists() or path.is_symlink():
            parser.error('Already exists; choose a new name: ' + str(path.relative_to(ROOT)))

    source = json.loads((ROOT / 'configs/source-boston.example.json').read_text())
    source['source_version'] = name
    source['streetview']['sample_size'] = args.sample_size
    workflow = json.loads((ROOT / 'configs/workflow-boston-small.json').read_text())
    scope = (str(args.sample_size) + '-cell pilot') if args.sample_size else 'all eligible cells in the configured bounding box'
    workflow.update({
        'source_config': str(source_file.relative_to(ROOT)),
        'source_directory': str(workspace.relative_to(ROOT)),
        'snapshot_version': snapshot_version,
        'mode': 'full',
        'max_new_label_images': args.label_limit,
        'result_label': name + ': ' + scope,
        'scope_note': 'Use all qualifying prepared rows from ' + scope + '; collection filters still apply.',
    })
    # Exclusive creation also protects against a concurrent generator using this name.
    created = []
    try:
        for path, value in [(source_file, source), (workflow_file, workflow)]:
            with path.open('x', encoding='utf-8') as stream:
                created.append(path)
                json.dump(value, stream, indent=2)
                stream.write('\n')
    except Exception:
        for path in created:
            path.unlink()
        raise

    print('Created configs only. No data collection, paid labeling or training was started.')
    for path in [source_file, workflow_file]:
        print('Config: ' + str(path.relative_to(ROOT)))
    print('Source version: ' + name)
    print('Workspace: ' + str(workspace.relative_to(ROOT)))
    print('Snapshot: ' + snapshot_version)
    print('Cell selection: ' + scope)
    print('New-label image limit: ' + str(args.label_limit) + ' per invocation; retries may add requests.')
    print('\nRun from the repository root when ready (permits paid labeling):')
    print('make run-all WORKFLOW_CONFIG=' + str(workflow_file.relative_to(ROOT)) +
          ' WORKFLOW_ARGS="--run-id ' + name + ' --allow-labeling"')
    print('For a later resume, add --resume inside WORKFLOW_ARGS; keep the configs unchanged.')


if __name__ == '__main__':
    main()
