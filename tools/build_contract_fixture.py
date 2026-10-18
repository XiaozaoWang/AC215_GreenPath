"""Export traceable step-1 fixtures from existing experiments; does not train.
Requires pandas, pyarrow and h3 (the existing data notebook environment).
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_json(p, value):
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def index_files(root):
    return {p.relative_to(root).as_posix(): {'sha256': digest(p), 'size_bytes': p.stat().st_size}
            for p in sorted(root.rglob('*')) if p.is_file() and p.name != 'manifest.json'}


def main():
    import h3
    import pandas as pd
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--snapshot-output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    data, out = args.data_root, args.output
    if out.exists():
        raise ValueError('Choose a new output directory; existing artifact versions are immutable')
    experiment = 'baselines-first-run-20261013T0242'
    dataset = data / 'datasets/v1'
    results = data / 'results' / experiment
    ds = pd.read_parquet(dataset / 'dataset.parquet')
    source_schema = json.loads((dataset / 'schema.json').read_text())
    ds_manifest = json.loads((dataset / 'manifest.json').read_text())
    predictions = pd.read_parquet(results / 'predictions.parquet')
    ids = ds.loc[ds.split == 'test', ['cell_id', 'h3_index']].sort_values('cell_id')
    predictions = predictions[predictions.split == 'test']
    if predictions.cell_id.duplicated().any():
        raise ValueError('duplicate prediction IDs')
    if set(predictions.cell_id) != set(ids.cell_id):
        raise ValueError('source test predictions do not match the frozen dataset test IDs')
    merged = ids.merge(predictions, on='cell_id', how='left', validate='one_to_one')
    dims = source_schema['targets']
    if merged[[f'M1__{d}' for d in dims]].isna().any().any():
        raise ValueError('Missing baseline predictions')
    active_schema = json.loads((root / 'api-service/api/dimensions.json').read_text())
    if list(active_schema['dimensions']) != dims:
        raise ValueError('Model target order differs from the shared schema')
    out.mkdir(parents=True)
    write_json(out / 'schema.json', active_schema)
    features, scores = [], []
    for row in merged.to_dict('records'):
        coords = [[lon, lat] for lat, lon in h3.cell_to_boundary(row['h3_index'])]
        coords.append(coords[0])
        features.append({'type': 'Feature', 'id': row['cell_id'], 'properties': {'cell_id': row['cell_id'], 'h3_index': row['h3_index']}, 'geometry': {'type': 'Polygon', 'coordinates': [coords]}})
        scores.append({'cell_id': row['cell_id'], 'scores': {d: float(row[f'M1__{d}']) for d in dims}})
    write_json(out / 'region.geojson', {'type': 'FeatureCollection', 'features': features})
    write_json(out / 'scores.json', scores)
    shutil.copyfile(dataset / 'manifest.json', out / 'source-dataset-manifest.json')
    shutil.copyfile(results / 'config.json', out / 'source-experiment-config.json')
    current_inputs = {}
    for name, recorded in ds_manifest['inputs'].items():
        current = digest(data / name)
        current_inputs[name] = {'recorded_sha256_prefix': recorded, 'current_sha256': current, 'matches_recorded': current.startswith(recorded)}
    write_json(out / 'provenance.json', {
        'experiment': experiment, 'dataset_version': 'v1', 'model': 'M1',
        'prediction_role': 'held_out_test', 'n_cells': len(scores),
        'source_files': {str(p.relative_to(data)): digest(p) for p in [dataset / 'dataset.parquet', results / 'predictions.parquet', results / 'config.json']},
        'upstream_input_audit': current_inputs,
        'limitation': 'Saved predictions from an existing frozen baseline experiment. No model weights are included and no new-area inference is provided. Current processed tables with mismatching hashes are not published as original v1 inputs. Labels remain VLM-generated, not human-validated.'})
    manifest = {
        'artifact_version': 'boston-heldout-v1', 'format_version': 'greenpath-region-v1',
        'schema_version': active_schema['schema_version'], 'schema_file': 'schema.json',
        'dataset_version': 'v1', 'model_version': 'm1-clip-baseline-20261013T0242',
        'region_id': 'boston-heldout-v1', 'region_name': 'Boston · held-out H3 cells',
        'prediction_source': 'saved_heldout_baseline',
        'description': 'Real M1 baseline test predictions; fixed-region fixture, not live inference.',
        'geometry_file': 'region.geojson', 'scores_file': 'scores.json',
        'model_output': {'format': 'records', 'id_field': 'cell_id', 'scores_field': 'scores'},
        'rmse': None, 'files': index_files(out)}
    write_json(out / 'manifest.json', manifest)
    if args.snapshot_output:
        snap = args.snapshot_output
        if snap.exists():
            raise ValueError('Snapshot output already exists')
        snap.mkdir(parents=True)
        for name in ['dataset.parquet', 'train.parquet', 'val.parquet', 'test.parquet', 'schema.json', 'DATASET_CARD.md']:
            shutil.copyfile(dataset / name, snap / name)
        shutil.copyfile(dataset / 'manifest.json', snap / 'upstream-manifest.json')
        write_json(snap / 'provenance.json', {'upstream_input_audit': current_inputs, 'scope': 'Frozen v1 tabular export only. Referenced image files and original raw inputs are not included. This is not a full training snapshot.'})
        write_json(snap / 'manifest.json', {'artifact_version': 'v1-tabular-export-v1', 'dataset_version': 'v1', 'scope': 'tabular-only', 'files': index_files(snap)})
    print(json.dumps({'output': str(out), 'n_cells': len(scores), 'n_dimensions': len(dims), 'source': 'held_out_test', 'upstream_audit': current_inputs}, ensure_ascii=False))


if __name__ == '__main__':
    main()
