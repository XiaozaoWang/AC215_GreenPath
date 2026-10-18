"""Online request flow. The batch training pipeline is a separate development step."""
import time
from api import agent, model_client, score_math, tiles
from api.dimensions import PROVIDER, SCHEMA_VERSION, SPEC
from api.schemas import ScoreResponse


class UnknownRegionError(Exception):
    pass


def capabilities(version_id=None):
    if PROVIDER == 'bundle':
        from api.model_bundle import get_bundle
        bundle = get_bundle(version_id)
        m = bundle.manifest
        from api.versions import resolve
        entry, _ = resolve(m['artifact_version'])
        regions = [{'id': m['region_id'], 'label': m['region_name']}]
        model = {k: m[k] for k in ['model_version', 'dataset_version', 'prediction_source', 'description']}
        model.update({'version_id': m['artifact_version'], 'source_version': bundle.provenance.get('source_workspace_version') or bundle.provenance.get('source_version', m.get('source_version')), 'snapshot_version': bundle.provenance.get('snapshot_version') or bundle.provenance.get('source_version'), 'run_id': bundle.provenance.get('run_id') or entry.get('run_id') or m.get('run_id'), 'n_cells': len(bundle.tiles), 'mode': bundle.provenance.get('mode', 'historical-heldout'), 'evaluation_note': bundle.provenance.get('evaluation_note', 'Historical held-out predictions; see experiment provenance.')})
        model['rmse_source'] = 'model' if m.get('rmse') is not None else f'placeholder: {score_math.PLACEHOLDER_RMSE:g} (5% of score range, not measured error)'
    else:
        if version_id not in (None, 'legacy-fake'):
            from api.versions import UnknownVersionError
            raise UnknownVersionError('Unknown legacy version')
        regions = [{'id': 'demo', 'label': 'Legacy simulated square grid'}]
        model = {'version_id': 'legacy-fake', 'n_cells': 10000, 'mode': 'simulated', 'model_version': 'legacy-fake', 'dataset_version': 'legacy-demo', 'prediction_source': 'simulated', 'description': 'Simulated scores; not real model inference.', 'rmse_source': 'placeholder: 0.05'}
    return {'schema_version': SCHEMA_VERSION, 'dimensions': SPEC['dimensions'], 'score_range': SPEC['score_range'], 'examples': SPEC.get('examples', []), 'regions': regions, 'default_region_id': regions[0]['id'], **model}


def run_score(text: str, region_id: str | None = None, get_preference=agent.get_preference, version_id: str | None = None) -> ScoreResponse:
    meta = capabilities(version_id)
    region_id = region_id or meta['default_region_id']
    try:
        tile_list = tiles.get_tiles(region_id, meta.get("version_id")) if PROVIDER == "bundle" else tiles.get_tiles(region_id)
    except ValueError as error:
        raise UnknownRegionError(str(error)) from error
    timings = {}
    clock = time.perf_counter()
    result = get_preference(text)
    timings['agent'] = round((time.perf_counter() - clock) * 1000, 1)
    weights = result.preference.as_vector()
    clock = time.perf_counter()
    scores = model_client.predict(tile_list, meta.get("version_id")) if PROVIDER == "bundle" else model_client.predict(tile_list)
    timings['model'] = round((time.perf_counter() - clock) * 1000, 1)
    clock = time.perf_counter()
    if PROVIDER == 'bundle':
        from api.model_bundle import get_bundle
        values, stats = score_math.score_values(scores, weights, result.preference.must_have, get_bundle(meta.get('version_id')).manifest.get('rmse'))
        features = [{'type': 'Feature', 'id': t.tile_id, 'geometry': t.geometry, 'properties': {'cell_id': t.tile_id, 'h3_index': t.h3_index, 'value': v, 'scores': t.scores}} for t, v in zip(scores, values)]
        heatmap, rows, cols = None, 0, 0
    else:
        heatmap, stats = score_math.combine(scores, weights, result.preference.must_have)
        features, rows, cols = [], len(heatmap), len(heatmap[0])
    timings['score'] = round((time.perf_counter() - clock) * 1000, 1)
    return ScoreResponse(version_id=meta.get("version_id"), dataset_version=meta.get("dataset_version"), source_version=meta.get("source_version"), snapshot_version=meta.get("snapshot_version"), run_id=meta.get("run_id"), region_id=region_id, agent=result, weights=weights, stats=stats,
        timings_ms=timings, features=features, rows=rows, cols=cols, heatmap=heatmap,
        schema_version=SCHEMA_VERSION, model_version=meta['model_version'], prediction_source=meta['prediction_source'], description=meta['description'], rmse_source=meta['rmse_source'])


def preview(version_id=None):
    """Display a saved model dimension without calling a preference model."""
    from api.schemas import AgentResult, DimensionWeight, Preference
    dimension = 'shade' if 'shade' in SPEC['dimensions'] else next(iter(SPEC['dimensions']))
    parsed = AgentResult(preference=Preference(weights=[DimensionWeight(
        dimension=dimension, weight=1, reason='Saved prediction preview; no preference model was called')]),
        attempts=[], used_fallback=False)
    result = run_score('', get_preference=lambda _: parsed, version_id=version_id)
    return result.model_copy(update={'preview': True})
