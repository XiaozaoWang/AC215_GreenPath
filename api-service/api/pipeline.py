# When score calls return pipeline.run_score(request.text, request.region_id), this arranges the flow: call each module in order and turn one sentence and the vision model results into a heatmap.
# It's split out of score so we can test the whole flow without starting the server

import time
from api import agent, model_client, score_math, tiles
from api.schemas import ScoreResponse

# Raise this error when the region id is unknown; the router catches it and turns it into HTTP 404
# A separate error type is defined here so other ValueErrors (like data check failures) aren't mistaken for 404 too
class UnknownRegionError(Exception):
    pass


# Full flow: user's words + region scores already processed by the vision model → heatmap + the intermediate results of every step
# get_preference can be swapped for a fake one: tests don't call Gemini and cost nothing
def run_score(text: str, region_id: str, get_preference=agent.get_preference) -> ScoreResponse:
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
        tile_list = tiles.get_tiles(region_id)
    except ValueError as e:
        raise UnknownRegionError(str(e)) from e
    lap("tiles")

    # 3. Fill the scores from the vision model into the empty tile list made in the step above
    tile_scores = model_client.predict(tile_list)
    lap("model")

    # 4. Raw scores + weights → heatmap
    heatmap, stats = score_math.combine(tile_scores, weights, agent_result.preference.must_have)
    lap("score")

    # 5. Return to score.py
    return ScoreResponse(
        region_id=region_id,
        agent=agent_result,
        weights=weights,
        rows=len(heatmap),
        cols=len(heatmap[0]),
        heatmap=heatmap,
        stats=stats,
        timings_ms=timings,
    )
