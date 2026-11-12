"""Route planning: request/response contract and the orchestration of preference parsing, prediction and search.

Three entry points share one implementation:
  plan()        shortest route and the cells that will be predicted (instant, needs no text)
  plan_route()  the full result in one response
  stream()      the same work as plan_route(), reported as events while it runs (newline-delimited JSON)
"""
import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from api import agent, baseline, model_client, road_graph, route_search
from api.dimensions import DIMENSION_NAMES
from api.schemas import DimensionWeight, Tile

RouteKind = Literal["shortest", "efficient", "balanced", "preferred"]


class Coordinate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RoutePlanRequest(BaseModel):
    origin: Coordinate
    destination: Coordinate
    region_id: str = "city_ma_boston_big"


class RouteRequest(RoutePlanRequest):
    text: str = Field(default="", max_length=2000)


class RouteGeometry(BaseModel):
    type: Literal["LineString"] = "LineString"
    coordinates: list[tuple[float, float]] = Field(min_length=2)    # GeoJSON order: (lon, lat)


class RouteOption(BaseModel):
    kind: RouteKind
    geometry: RouteGeometry
    distance_m: float = Field(ge=0)
    detour: float = Field(description="Extra length over the shortest route as a fraction (0.12 = 12 % longer)")
    match: float = Field(ge=0, le=1, description="Share of the route that fits the preference (1 = all of it)")
    alternative: bool = Field(default=False, description="True for an option that is no better than another route on both length and match "
                                                         "(for example the shortest route): it only offers other streets")
    profile: dict[str, float] = Field(description="Mean predicted score along the route for each dimension the user cares about")


class RouteCandidate(BaseModel):
    """One route found while searching; kind is set when it was offered as an option."""
    detour: float
    match: float
    kind: RouteKind | None


class RoutePlan(BaseModel):
    region_id: str
    shortest: RouteOption
    cells: list[str]                   # H3 cells that will be predicted, nearest to the shortest route first


class RouteResponse(BaseModel):
    region_id: str
    geometry: RouteGeometry            # the recommended route, for clients that show a single route
    distance_m: float = Field(ge=0)
    routes: list[RouteOption]          # the shortest route first, then the distinct preference-based options
    recommended: RouteKind
    weights: list[DimensionWeight]     # what was understood from the text (empty without a preference)
    must_have: list[str]
    intensity: float                   # 0 to 1: how strongly the user wants it
    used_fallback: bool
    candidates: list[RouteCandidate]   # every distinct route found, to show the length / match trade-off
    max_detour: float                  # the extra length the search ended up allowing (it widens until three options exist)
    improved: bool                     # at least one option fits the preference better than the shortest route
    area_mean: dict[str, float]        # area-wide mean score of each dimension the user cares about
    cells_scored: int
    cells_without_score: int
    timings_ms: dict[str, float]


def _scores_matrix(predictions) -> np.ndarray:
    # Cells the Model Service could not score become NaN rows.
    rows = [[np.nan] * len(DIMENSION_NAMES) if p.scores is None else [p.scores[name] for name in DIMENSION_NAMES]
            for p in predictions]
    return np.array(rows, dtype=float).reshape(-1, len(DIMENSION_NAMES))


def _vector(preference) -> np.ndarray:
    return np.array(list(preference.as_vector().values())) if preference else np.zeros(len(DIMENSION_NAMES))


def prepare(request: RoutePlanRequest):
    """Load the graph and compute the shortest route and the detour ellipse; raises UnknownGraphError / NoRouteError."""
    started = time.perf_counter()
    graph = road_graph.load(request.region_id)
    prep = route_search.prepare(graph, (request.origin.lon, request.origin.lat), (request.destination.lon, request.destination.lat),
                                route_search.LADDER[0])
    prep.prepare_ms = round((time.perf_counter() - started) * 1000, 1)
    return graph, prep


def _shortest_option(graph, prep, match: float = 0.0) -> RouteOption:
    return RouteOption(kind="shortest", geometry=RouteGeometry(coordinates=graph.route_coords(prep.base_nodes, prep.base_edges)),
                       distance_m=round(prep.base_length, 1), detour=0.0, match=match, profile={})


def plan(request: RoutePlanRequest) -> RoutePlan:
    graph, prep = prepare(request)
    return RoutePlan(region_id=request.region_id, shortest=_shortest_option(graph, prep), cells=prep.cell_ids(graph))


def _run(request: RouteRequest, graph, prep, emit) -> RouteResponse:
    """Do the whole job, calling emit(event) with progress events (see stream()).

    The cells under the detour ellipse are predicted first. If the preference does not give three different routes inside
    it, the maximum detour is widened step by step (route_search.LADDER); only the cells that are new are predicted."""
    timings, last = {"prepare": prep.prepare_ms}, time.perf_counter()

    def lap(name):
        nonlocal last
        now = time.perf_counter()
        timings[name] = round((now - last) * 1000, 1)
        last = now

    reference = baseline.load(request.region_id)
    text = request.text.strip()
    start, end = (request.origin.lon, request.origin.lat), (request.destination.lon, request.destination.lat)
    scores: dict[str, np.ndarray] = {}      # raw scores of every cell asked for so far (NaN row = no prediction)
    parsing, streamed = None, False

    def predict(cell_ids):
        """Predict the cells that have no scores yet; a finished batch is stored and coloured as soon as it is back."""
        new = [c for c in cell_ids if c not in scores]
        if not new:
            return

        def on_batch(rows):
            nonlocal streamed
            matrix = _scores_matrix(rows)
            for row, vector in zip(rows, matrix):
                scores[row.h3_index] = vector
            if reference is None:
                return
            preference = parsing.result().preference         # colours need the preference (usually ready before the first batch)
            direction, intensity = route_search.split_preference(_vector(preference))
            if intensity == 0:
                return
            must_have = [DIMENSION_NAMES.index(name) for name in preference.must_have]
            mismatch = route_search.cell_mismatch(matrix, direction, must_have, reference)
            emit({"type": "cells", "values": [[row.h3_index, None if np.isnan(m) else round(1 - float(m), 3)]
                                              for row, m in zip(rows, mismatch)]})
            streamed = True

        rows = model_client.predict([Tile(h3_index=c) for c in new], on_batch)
        for row, vector in zip(rows, _scores_matrix(rows)):      # the same rows again, in case the client does not report batches
            scores[row.h3_index] = vector

    def parse():
        result = agent.get_preference(text)
        preference = result.preference
        emit({"type": "preference", "weights": [w.model_dump() for w in preference.weights if w.weight != 0],
              "must_have": list(preference.must_have), "intensity": round(route_search.split_preference(_vector(preference))[1], 3),
              "used_fallback": result.used_fallback})
        return result

    parsed = None
    if text:
        emit({"type": "start", "cells_total": len(prep.cells)})
        # The cells under the ellipse do not depend on the text, so the Model Service starts at once, in parallel with Gemini.
        with ThreadPoolExecutor(max_workers=2) as pool:
            parsing = pool.submit(parse)
            predicting = pool.submit(predict, prep.cell_ids(graph))
            parsed = parsing.result()
            predicting.result()
    lap("parse_and_predict")

    preference = parsed.preference if parsed else None
    weights = _vector(preference)
    must_have = [DIMENSION_NAMES.index(name) for name in preference.must_have] if preference else []
    scored = bool(scores)

    def matrix_for(p):
        ids = p.cell_ids(graph)
        return np.array([scores[c] for c in ids]).reshape(-1, len(DIMENSION_NAMES)) if scored else np.full((len(ids), len(DIMENSION_NAMES)), np.nan)

    ladder = route_search.LADDER
    for level, max_detour in enumerate(ladder):
        if level > 0:
            prep = route_search.prepare(graph, start, end, max_detour)
            emit({"type": "expand", "max_detour": max_detour, "cells_added": sum(c not in scores for c in prep.cell_ids(graph))})
            predict(prep.cell_ids(graph))
        X = matrix_for(prep)
        result = route_search.search(graph, prep, X, weights, must_have, reference, add_alternatives=level == len(ladder) - 1)
        if not scored or result.intensity == 0 or len(result.options) >= route_search.WANTED_OPTIONS:
            break
    if scored and not streamed and result.intensity > 0:
        # Without an area baseline the colours are relative to this request, so they are sent once, at the end.
        emit({"type": "cells", "values": [[graph.cell_list[i], None if np.isnan(m) else round(1 - float(m), 3)]
                                          for i, m in zip(prep.cells, result.mismatch)]})
    lap("search")

    cared = [i for i in range(len(DIMENSION_NAMES)) if weights[i] != 0]

    def option(kind, candidate):
        values = route_search.profile(graph, prep.cells, X, candidate.edges, cared) if scored else {}
        return RouteOption(
            kind=kind, geometry=RouteGeometry(coordinates=graph.route_coords(candidate.nodes, candidate.edges)),
            distance_m=round(candidate.length_m, 1), detour=round(candidate.length_m / prep.base_length - 1, 4),
            match=round(candidate.match, 4), profile={DIMENSION_NAMES[i]: round(v, 4) for i, v in values.items()})

    routes = [option("shortest", result.shortest)] + [option(kind, c) for kind, c in result.options.items()]
    for r in routes[1:]:        # an option that another route (the shortest one included) beats on length and on match only offers other streets
        r.alternative = any(o is not r and o.distance_m <= r.distance_m and o.match >= r.match - route_search.MIN_GAIN for o in routes)
    chosen = next(r for r in routes if r.kind == result.recommended)
    kind_of = {id(c): kind for kind, c in result.options.items()} | {id(result.shortest): "shortest"}
    candidates = [RouteCandidate(detour=round(c.length_m / prep.base_length - 1, 4), match=round(c.match, 4), kind=kind_of.get(id(c)))
                  for c in result.candidates]
    area_mean = {DIMENSION_NAMES[i]: round(float(reference[:, i].mean()), 4) for i in cared} if reference is not None else {}
    lap("response")

    return RouteResponse(
        region_id=request.region_id, geometry=chosen.geometry, distance_m=chosen.distance_m, routes=routes,
        recommended=result.recommended, weights=[w for w in (preference.weights if preference else []) if w.weight != 0],
        must_have=list(preference.must_have) if preference else [], intensity=round(result.intensity, 3),
        used_fallback=parsed.used_fallback if parsed else False, candidates=candidates, max_detour=prep.max_detour,
        improved=result.improved, area_mean=area_mean,
        cells_scored=len(prep.cells) if scored else 0,
        cells_without_score=int(np.isnan(X).any(axis=1).sum()) if scored else 0, timings_ms=timings)


def plan_route(request: RouteRequest) -> RouteResponse:
    graph, prep = prepare(request)
    return _run(request, graph, prep, lambda event: None)


def error_status(exc: Exception) -> int:
    if isinstance(exc, model_client.ModelTimeoutError):
        return 504
    return 502 if isinstance(exc, model_client.ModelServiceError) else 500


def stream(request: RouteRequest, graph, prep):
    """Yield newline-delimited JSON events while the route is being planned.

    Events: start {cells_total}, preference {weights, must_have, intensity, used_fallback},
    cells {values: [[h3_index, match or null], ...]} (match 0 = poor, 1 = good; sent per finished batch),
    expand {max_detour, cells_added} (the search widens the allowed extra length to find more options),
    then result {...RouteResponse} or error {status, detail}."""
    events: queue.Queue = queue.Queue()

    def work():
        try:
            events.put({"type": "result", **_run(request, graph, prep, events.put).model_dump()})
        except Exception as exc:  # reported to the client as an event because the HTTP status is already sent
            events.put({"type": "error", "status": error_status(exc), "detail": str(exc) or exc.__class__.__name__})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()
    while (event := events.get()) is not None:
        yield json.dumps(event, separators=(",", ":")) + "\n"
