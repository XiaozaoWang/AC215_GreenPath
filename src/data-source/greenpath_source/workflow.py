"""Run the four source stages in one process with verified resume decisions."""
import hashlib
import json
from pathlib import Path
import runpy

from .runtime import digest, write_json

STAGES = [('01_streetview', 'streetview'), ('02_tiles', 'tiles'),
          ('03_osm_features', 'features')]


def workspace_path(workspace, relative):
    path = (Path(workspace) / relative).resolve()
    if not path.is_relative_to(Path(workspace).resolve()):
        raise ValueError('Stage manifest path escapes the source workspace')
    return path


def stage_ready(workspace, stage, spec, code_directory=None):
    workspace = Path(workspace)
    records = sorted((workspace / 'runs').glob('*/' + stage + '_manifest.json'))
    if not records:
        return False
    record = json.loads(records[-1].read_text())
    module = dict(STAGES)[stage]
    code_directory = Path(code_directory) if code_directory else Path(__file__).parent
    expected = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    if record.get('config_sha256') != expected or record.get('implementation_sha256') != digest(code_directory / (module + '.py')):
        return False
    for relative, checksum in record['output_sha256'].items():
        # Tiles extends the master table; the original streetview lineage is retained.
        if stage == '01_streetview' and relative == 'processed/cells.parquet':
            continue
        file = workspace_path(workspace, relative)
        if not file.is_file() or digest(file) != checksum:
            return False
    if stage != '01_streetview':
        for relative, checksum in record.get('input_sha256', {}).items():
            if stage == '02_tiles' and relative == 'processed/cells.parquet':
                continue
            file = workspace_path(workspace, relative)
            if not file.is_file() or digest(file) != checksum:
                return False
    return True


def run_stage(stage, options):
    runpy.run_module('greenpath_source.' + stage, run_name='__main__',
                     init_globals={'SOURCE_OPTIONS': options})


def run_all(options, allow_labeling=False):
    """The CLI holds one workspace lock across all stages, including paid calls."""
    root, spec = options['root'], options['spec']
    marker = root / 'source-workspace.json'
    if marker.exists():
        identity = json.loads(marker.read_text())
        expected = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        if identity['source_version'] != spec['source_version'] or identity['config_sha256'] != expected:
            raise ValueError('Workspace belongs to another configuration; use a new source version and directory')
    meta = root / 'processed/labels_meta.json'
    complete = meta.is_file() and json.loads(meta.read_text()).get('complete') is True
    if not complete and not allow_labeling:
        raise ValueError('Incomplete labels: add --allow-labeling and a positive --max-images limit before acquisition')
    record = {'source_version': spec['source_version'], 'success': False, 'stages': {}}
    summary = root / 'source-run-summary.json'
    try:
        for name, module in STAGES:
            if stage_ready(root, name, spec):
                record['stages'][name] = 'reused_verified_outputs'
                print(json.dumps({'stage': name, 'status': 'reused_verified_outputs'}), flush=True)
            else:
                run_stage(module, options)
                if not stage_ready(root, name, spec):
                    raise ValueError('Stage output verification failed: ' + name)
                record['stages'][name] = 'completed'
            write_json(summary, record)
        run_stage('labeling', options)
        labels = json.loads(meta.read_text())
        if not labels.get('complete') or labels.get('images_pending') != 0:
            raise ValueError('Label batch finished with %s images pending; cached outputs are saved. Resume explicitly for another bounded batch.' % labels.get('images_pending'))
        record['stages']['04_labeling'] = 'complete_selected_workspace'
        record['success'] = True
    except Exception as error:
        record['error'] = str(error)
        raise
    finally:
        # Do not create a summary inside an uninitialized workspace: streetview
        # deliberately requires an empty directory when first assigning ownership.
        if marker.exists():
            write_json(summary, record)
    return record
