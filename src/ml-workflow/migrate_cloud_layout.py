"""Copy known legacy GCS artifacts to mirrored storage; never delete old objects.

Requires google-auth and requests when --apply uses the host transfer backend.
Run without --apply to print the migration plan only.
"""
import argparse
from pathlib import Path
import json
import tempfile

from artifact_catalog import ARTIFACTS
from runtime_environment import environment
from manage_artifacts import artifact_tool, publish_run
from cloud_layout import ROOT, DEFAULT_ROOT

LEGACY = {
    'pipeline-inputs/boston-processed-v1': 'snapshots/sources/boston-processed-v1',
    'encoders/clip-b16-laion2b-v1': 'encoders/clip-b16-laion2b-v1',
    'serving/boston-heldout-en-v2': 'serving/boston-heldout-en-v2',
    'serving/boston-heldout-v1': 'serving/boston-heldout-v1',
    'models/m1-clip-ridge-v1': 'reference/models/m1-clip-ridge-v1',
    'datasets/v1-tabular-export-v1': 'reference/v1-tabular-export-v1',
    'samples/boston-image-smoke-v1': 'reference/samples/boston-image-smoke-v1',
    'training-inputs/m1-refit-inputs-v1': 'reference/training-inputs/m1-refit-inputs-v1',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--legacy-root', default='gs://ac215-greenpath')
    parser.add_argument('--gcs-root', default=DEFAULT_ROOT)
    parser.add_argument('--run-id', action='append', default=[])
    args = parser.parse_args()
    print(json.dumps({'legacy_artifacts': LEGACY, 'runs': args.run_id,
                      'destination': args.gcs_root, 'delete_old_objects': False}, indent=2), flush=True)
    if not args.apply:
        return
    env = environment(ROOT)
    env['GREENPATH_ARTIFACT_TRANSFER'] = 'host'
    ARTIFACTS.mkdir(exist_ok=True)
    for old, new in LEGACY.items():
        print('Migrating: ' + old + ' -> ' + new, flush=True)
        with tempfile.TemporaryDirectory(prefix='.migration-', dir=ARTIFACTS) as directory:
            local = Path(directory) / 'artifact'
            artifact_tool('fetch', args.legacy_root.rstrip('/') + '/' + old, local, env, build=False)
            artifact_tool('publish', args.gcs_root.rstrip('/') + '/' + new, local, env, build=False)
    for identifier in args.run_id:
        print('Publishing mirrored run: ' + identifier, flush=True)
        result = publish_run(identifier, args.gcs_root, env, no_build=True, include_upstream=True)
        print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
