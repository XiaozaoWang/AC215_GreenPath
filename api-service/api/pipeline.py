"""Online request flow. The batch training pipeline is a separate development step."""
import time
from api import agent, model_client, score_math, tiles
from api.dimensions import PROVIDER, SCHEMA_VERSION, SPEC
from api.schemas import ScoreResponse


class UnknownRegionError(Exception):
    pass


def capabilities():
    if PROVIDER == 'bundle':
        from api.model_bundle import get_bundle
        m = get_bundle().manifest
        regions = [{'id': m['region_id'], 'label': m['region_name']}]
        model = {k: m[k] for k in ['model_version', 'dataset_version', 'prediction_source', 'description']}
        model['rmse_source'] = 'model' if m.get('rmse') is not None else f'placeholder: {score_math.PLACEHOLDER_RMSE:g} (5% of score range, not measured error)'
    else:
        regions = [{'id': 'demo', 'label': 'Legacy simulated square grid'}]
        model = {'model_version': 'legacy-fake', 'dataset_version': 'legacy-demo', 'prediction_source': 'simulated', 'description': 'Simulated scores; not real model inference.', 'rmse_source': 'placeholder: 0.05'}
    return {'schema_version': SCHEMA_VERSION, 'dimensions': SPEC['dimensions'], 'score_range': SPEC['score_range'], 'examples': SPEC.get('examples', []), 'regions': regions, 'default_region_id': regions[0]['id'], **model}


def run_score(text: str, region_id: str | None = None, get_preference=agent.get_preference) -> ScoreResponse:
    meta = capabilities()
    region_id = region_id or meta['default_region_id']
    try:
        tile_list = tiles.get_tiles(region_id)
    except ValueError as error:
        raise UnknownRegionError(str(error)) from error
    timings = {}
    clock = time.perf_counter()
    result = get_preference(text)
    timings['agent'] = round((time.perf_counter() - clock) * 1000, 1)
    weights = result.preference.as_vector()
    clock = time.perf_counter()
    scores = model_client.predict(tile_list)
    timings['model'] = round((time.perf_counter() - clock) * 1000, 1)
    clock = time.perf_counter()
    if PROVIDER == 'bundle':
        from api.model_bundle import get_bundle
        values, stats = score_math.score_values(scores, weights, result.preference.must_have, get_bundle().manifest.get('rmse'))
        features = [{'type': 'Feature', 'id': t.tile_id, 'geometry': t.geometry, 'properties': {'cell_id': t.tile_id, 'h3_index': t.h3_index, 'value': v, 'scores': t.scores}} for t, v in zip(scores, values)]
        heatmap, rows, cols = None, 0, 0
    else:
        heatmap, stats = score_math.combine(scores, weights, result.preference.must_have)
        features, rows, cols = [], len(heatmap), len(heatmap[0])
    timings['score'] = round((time.perf_counter() - clock) * 1000, 1)
    return ScoreResponse(region_id=region_id, agent=result, weights=weights, stats=stats,
        timings_ms=timings, features=features, rows=rows, cols=cols, heatmap=heatmap,
        schema_version=SCHEMA_VERSION, model_version=meta['model_version'], prediction_source=meta['prediction_source'], description=meta['description'], rmse_source=meta['rmse_source'])
