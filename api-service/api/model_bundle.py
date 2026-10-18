"""Adapter: versioned geographic data + records/matrix predictions -> TileScore.

No fitting or label reading happens here. Missing predictions are errors, not zeros.
"""
from functools import lru_cache
import json
import math
from pathlib import Path

from api.artifacts import safe_path, verify
from api.dimensions import BUNDLE_DIR, DIMENSION_NAMES, SCHEMA_VERSION, SPEC, load_spec
from api.schemas import Tile, TileScore


def adapt_predictions(payload, spec: dict, dimensions: list[str]) -> dict[str, dict]:
    output = {}
    if spec.get('format') == 'records':
        if not isinstance(payload, list):
            raise ValueError('records predictions must be a list')
        pairs = [(r[spec.get('id_field', 'cell_id')], r[spec.get('scores_field', 'scores')]) for r in payload]
    elif spec.get('format') == 'matrix':
        ids, values = payload['ids'], payload['values']
        order = payload['dimensions']
        if len(ids) != len(values) or len(order) != len(set(order)) or set(order) != set(dimensions):
            raise ValueError('matrix shape/dimensions mismatch')
        if any(len(row) != len(order) for row in values):
            raise ValueError('matrix row width mismatch')
        pairs = [(key, dict(zip(order, row))) for key, row in zip(ids, values)]
    else:
        raise ValueError('supported prediction formats: records, matrix')
    for key, scores in pairs:
        if not isinstance(key, str) or not key or key in output:
            raise ValueError(f'missing/duplicate prediction ID: {key!r}')
        if not isinstance(scores, dict) or set(scores) != set(dimensions):
            raise ValueError(f'prediction dimensions mismatch: {key}')
        output[key] = scores
    return output


def validate_geometry(geometry):
    if not isinstance(geometry, dict) or geometry.get('type') != 'Polygon':
        raise ValueError('this renderer supports GeoJSON Polygon geometry')
    rings = geometry.get('coordinates')
    if not isinstance(rings, list) or not rings:
        raise ValueError('empty polygon')
    for ring in rings:
        if len(ring) < 4 or ring[0] != ring[-1]:
            raise ValueError('polygon ring must be closed and have at least four positions')
        for position in ring:
            if len(position) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in position):
                raise ValueError('polygon needs finite longitude/latitude pairs')
            lon, lat = position
            if not -180 <= lon <= 180 or not -90 <= lat <= 90:
                raise ValueError('polygon coordinate out of range')


class ModelBundle:
    def __init__(self, root: Path):
        self.manifest = verify(root)
        if (root / "provenance.json").is_file() and "provenance.json" not in self.manifest["files"]:
            raise ValueError("Bundle provenance must be checksummed")
        self.provenance = json.loads((root / "provenance.json").read_text()) if (root / "provenance.json").is_file() else {}
        if not isinstance(self.provenance, dict):
            raise ValueError("Bundle provenance must be an object")
        m = self.manifest
        if m.get('format_version') != 'greenpath-region-v1':
            raise ValueError('unsupported region bundle format')
        for field in ['schema_version', 'schema_file', 'model_version', 'dataset_version', 'region_id', 'region_name', 'prediction_source', 'description', 'geometry_file', 'scores_file', 'model_output']:
            if not m.get(field):
                raise ValueError(f'missing bundle metadata: {field}')
        for field in ['schema_file', 'geometry_file', 'scores_file']:
            if m[field] not in m['files']:
                raise ValueError(f'{field} must be checksummed in the manifest')
        self.spec = load_spec(safe_path(root, m['schema_file']))
        if m['schema_version'] != SCHEMA_VERSION or self.spec != SPEC:
            raise ValueError('active API schema and model bundle schema differ; restart with the matching contract')
        region = json.loads(safe_path(root, m['geometry_file']).read_text())
        if region.get('type') != 'FeatureCollection' or not region.get('features'):
            raise ValueError('expected a nonempty geographic FeatureCollection')
        self.tiles = []
        seen = set()
        seen_h3 = set()
        for feature in region['features']:
            props = feature['properties']
            key = props['cell_id']
            h3_index = props.get('h3_index')
            if feature.get('type') != 'Feature' or feature.get('id') != key or key in seen:
                raise ValueError('invalid or duplicate region ID')
            if h3_index and h3_index in seen_h3:
                raise ValueError('duplicate H3 cell')
            seen.add(key); seen_h3.add(h3_index)
            validate_geometry(feature['geometry'])
            self.tiles.append(Tile(tile_id=key, h3_index=h3_index, geometry=feature['geometry']))
        scores = adapt_predictions(json.loads(safe_path(root, m['scores_file']).read_text()), m['model_output'], DIMENSION_NAMES)
        if set(scores) != seen:
            raise ValueError('region and prediction IDs must match exactly')
        self.scores = {tile.tile_id: TileScore(**tile.model_dump(), scores=scores[tile.tile_id]) for tile in self.tiles}
        rmse = m.get('rmse')
        if rmse is not None and (set(rmse) != set(DIMENSION_NAMES) or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in rmse.values())):
            raise ValueError('RMSE must be finite, nonnegative and cover every dimension')


@lru_cache(maxsize=16)
def _load_bundle(directory, manifest_sha256):
    return ModelBundle(Path(directory))


def get_bundle(version_id=None):
    from api.versions import resolve
    entry, checksum = resolve(version_id)
    return _load_bundle(str(entry['directory']), checksum)


# Preserve the previous cache-reset interface for local development and tests.
get_bundle.cache_clear = _load_bundle.cache_clear
