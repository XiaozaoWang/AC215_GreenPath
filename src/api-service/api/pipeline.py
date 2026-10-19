# When score calls return pipeline.run_score(request.text, request.region_id), this arranges the flow: call each module in order and turn one sentence and the vision model results into a heatmap.
# It's split out of score so we can test the whole flow without starting the server

import time
from api import agent, model_client, score_math, tiles
from api.schemas import ScoreResponse
from api.dimensions import SCHEMA_VERSION, SPEC
from api.model_bundle import get_bundle

# Raise this error when the region id is unknown; the router catches it and turns it into HTTP 404
# A separate error type is defined here so other ValueErrors (like data check failures) aren't mistaken for 404 too
class UnknownRegionError(Exception):
    pass


# Full flow: user's words + region scores already processed by the vision model → heatmap + the intermediate results of every step
# get_preference can be swapped for a fake one: tests don't call Gemini and cost nothing
def run_score(text: str, region_id: str | None = None, get_preference=agent.get_preference, version_id: str | None = None) -> ScoreResponse:
    meta = capabilities(version_id)
    region_id = region_id or meta["default_region_id"]
    version_id = meta["version_id"]

    timings = {}

    # Small timing tool: record how many milliseconds the current step took into the timings dict; used after every step below
    clock = [time.perf_counter()]
    def lap(name):
        now = time.perf_counter()
        timings[name] = round((now - clock[0]) * 1000, 1)
        clock[0] = now

    # 1. User's words → agent.get_preference wraps llm.ask as a harness, asks Gemini and checks the reply
    agent_result = get_preference(text)
    weights = agent_result.preference.as_vector() #a method defined in preference: only pull out the weights part and turn it into a dict; missing items are filled with 0
    lap("agent")

    # 2. Build an empty table from the region name (unknown region → UnknownRegionError)
    try:
        tile_list = tiles.get_tiles(region_id, version_id)
    except ValueError as e:
        raise UnknownRegionError(str(e)) from e
    lap("tiles")

    # 3. Fill the scores from the vision model into the empty tile list made in the step above
    tile_scores = model_client.predict(tile_list, version_id)
    lap("model")

    # 4. Raw scores + weights → heatmap
    rmse = get_bundle(version_id).manifest.get("rmse")
    values, stats = score_math.score_values(tile_scores, weights, agent_result.preference.must_have, rmse)
    features = [
        {"type": "Feature", "id": t.tile_id, "geometry": t.geometry,
         "properties": {"cell_id": t.tile_id, "h3_index": t.h3_index, "value": v, "scores": t.scores}}
        for t, v in zip(tile_scores, values)
    ]
    lap("score")

    # 5. Return to score.py
    return ScoreResponse(
        region_id=region_id,
        agent=agent_result,
        weights=weights,
        stats=stats,
        timings_ms=timings,
        features=features,
        version_id=version_id,
        dataset_version=meta["dataset_version"],
        source_version=meta["source_version"],
        snapshot_version=meta["snapshot_version"],
        run_id=meta["run_id"],
        schema_version=SCHEMA_VERSION,
        model_version=meta["model_version"],
        prediction_source=meta["prediction_source"],
        description=meta["description"],
        rmse_source=meta["rmse_source"],
    )


def capabilities(version_id: str | None = None) -> dict:
    from api.versions import resolve
    bundle = get_bundle(version_id)
    m = bundle.manifest
    p = bundle.provenance
    entry, _ = resolve(m["artifact_version"])
    return {
        "schema_version": SCHEMA_VERSION,
        "dimensions": SPEC["dimensions"],
        "score_range": SPEC["score_range"],
        "examples": SPEC.get("examples", []),
        "regions": [{"id": m["region_id"], "label": m["region_name"]}],
        "default_region_id": m["region_id"],
        "version_id": m["artifact_version"],
        "model_version": m["model_version"],
        "dataset_version": m["dataset_version"],
        "prediction_source": m["prediction_source"],
        "description": m["description"],
        "source_version": p.get("source_workspace_version") or p.get("source_version", m.get("source_version")),
        "snapshot_version": p.get("snapshot_version") or p.get("source_version"),
        "run_id": p.get("run_id") or entry.get("run_id") or m.get("run_id"),
        "n_cells": len(bundle.tiles),
        "mode": p.get("mode", "historical-heldout"),
        "evaluation_note": p.get("evaluation_note", "Historical held-out predictions; see experiment provenance."),
        "rmse_source": "model" if m.get("rmse") is not None
        else f"placeholder: {score_math.PLACEHOLDER_RMSE:g} (5% of score range, not measured error)",
    }


def preview(version_id: str | None = None) -> ScoreResponse:
    from api.schemas import AgentResult, DimensionWeight, Preference
    dimension = "shade" if "shade" in SPEC["dimensions"] else next(iter(SPEC["dimensions"]))
    parsed = AgentResult(
        preference=Preference(weights=[DimensionWeight(dimension=dimension, weight=1, reason="Saved prediction preview; no preference model was called")]),
        attempts=[],
        used_fallback=False,
    )
    result = run_score("", get_preference=lambda _: parsed, version_id=version_id)
    return result.model_copy(update={"preview": True})
