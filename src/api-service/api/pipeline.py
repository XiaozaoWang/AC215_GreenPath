"""Convert preferences and model predictions into an ordered heatmap response."""
import time

from api import agent, model_client, score_math, tiles
from api.schemas import CellValue, ScoreResponse


def run_score(text: str, region_id: str, get_preference=agent.get_preference) -> ScoreResponse:
    timings = {}
    last = time.perf_counter()

    def lap(name):
        nonlocal last
        now = time.perf_counter()
        timings[name] = round((now - last) * 1000, 1)
        last = now

    # Parse preferences, load region cells, predict scenery, then calculate the heatmap.
    result = get_preference(text)
    weights = result.preference.as_vector()
    lap("agent")
    tile_list = tiles.get_tiles(region_id)
    lap("tiles")
    predictions = model_client.predict(tile_list)
    lap("model")
    values, stats = score_math.combine(predictions, weights, result.preference.must_have)
    lap("score")

    return ScoreResponse(
        region_id=region_id, agent=result, weights=weights,
        cells=[CellValue(h3_index=row.h3_index, value=value, status=row.status)
               for row, value in zip(predictions, values)],
        stats=stats, timings_ms=timings,
    )
