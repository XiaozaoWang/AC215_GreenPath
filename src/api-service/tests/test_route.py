"""POST /route: preference parsing and model prediction are replaced by fakes; the road graph is a toy or the real Boston one."""
import json
import random

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import agent, baseline, model_client, road_graph
from api.dimensions import DIMENSION_NAMES
from api.schemas import AgentResult, DimensionWeight, Preference, TileScore
from api.service import app
from tests.test_route_search import FAN_CELLS, FAN_D, FAN_S, fan_canopy, fan_graph

client = TestClient(app)
BODY = {"origin": {"lon": FAN_S[0], "lat": FAN_S[1]}, "destination": {"lon": FAN_D[0], "lat": FAN_D[1]}, "text": "shady ride"}
KINDS = ["shortest", "efficient", "balanced", "preferred"]


def preference(weight=1.0, must_have=()):
    return AgentResult(preference=Preference(weights=[DimensionWeight(dimension="tree_canopy", reason="shade", weight=weight)],
                                             must_have=list(must_have)), attempts=[], used_fallback=False)


@pytest.fixture
def toy(monkeypatch):
    """A fan of five routes (8, 10, 12, 14 and 16 long) whose tree canopy grows with the length. Records what was sent to the model."""
    sent = []

    def predict(tiles, on_batch=None):
        sent.append([t.h3_index for t in tiles])
        rows = [TileScore(h3_index=t.h3_index, scores={**dict.fromkeys(DIMENSION_NAMES, 0.5), "tree_canopy": fan_canopy(t.h3_index)})
                for t in tiles]
        for i in range(0, len(rows), 2):                      # report two cells per batch, like the real client reports batches
            if on_batch:
                on_batch(rows[i:i + 2])
        return rows

    monkeypatch.setattr(road_graph, "load", lambda region_id: fan_graph())
    monkeypatch.setattr(baseline, "load", lambda region_id: None)
    monkeypatch.setattr(agent, "get_preference", lambda text: preference())
    monkeypatch.setattr(model_client, "predict", predict)
    return sent


def stream_events(body):
    with client.stream("POST", "/route/stream", json=body) as response:
        assert response.status_code == 200 and response.headers["content-type"].startswith("application/x-ndjson")
        return [json.loads(line) for line in response.iter_lines() if line]


def test_always_returns_the_shortest_route_and_three_different_options(toy):
    response = client.post("/route", json=BODY)
    assert response.status_code == 200
    data = response.json()
    routes = {r["kind"]: r for r in data["routes"]}
    assert [r["kind"] for r in data["routes"]] == KINDS and data["recommended"] == "preferred"
    assert len({json.dumps(r["geometry"]) for r in routes.values()}) == 4                  # four different routes
    # +50 % holds only two better routes, so the limit widened to +75 % and the third option is the 14 long route
    assert data["max_detour"] == 0.75 and data["improved"]
    assert [routes[k]["distance_m"] for k in KINDS] == [8.0, 10.0, 12.0, 14.0]
    assert routes["preferred"]["match"] == max(r["match"] for r in routes.values()) > routes["shortest"]["match"]
    assert all(r["detour"] <= data["max_detour"] + 1e-9 for r in routes.values())
    assert routes["preferred"]["profile"] == {"tree_canopy": 0.9} and routes["shortest"]["profile"] == {"tree_canopy": 0.0}
    assert data["geometry"] == routes["preferred"]["geometry"] and data["distance_m"] == 14.0   # single-route fields mirror the recommendation
    assert data["weights"][0]["dimension"] == "tree_canopy" and data["intensity"] == 1.0
    assert data["cells_scored"] == 8 and data["cells_without_score"] == 0 and not data["used_fallback"]
    assert set(data["timings_ms"]) == {"prepare", "parse_and_predict", "search", "response"}


def test_the_detour_limit_widens_and_only_the_new_cells_are_predicted(toy):
    events = stream_events(BODY)
    expands = [e for e in events if e["type"] == "expand"]
    assert [(e["max_detour"], e["cells_added"]) for e in expands] == [(0.75, 2)]
    assert events[0]["cells_total"] == 6                                                 # +50 %: the 8, 10 and 12 long routes
    assert [len(call) for call in toy] == [6, 2]                                         # then only the two new cells were predicted
    assert events[-1]["type"] == "result" and events[-1]["max_detour"] == 0.75


def test_no_widening_when_the_first_limit_already_gives_three_options(toy, monkeypatch):
    monkeypatch.setattr("api.route_search.LADDER", (1.0, 1.5))
    events = stream_events(BODY)
    assert [e for e in events if e["type"] == "expand"] == [] and events[-1]["max_detour"] == 1.0
    assert [r["kind"] for r in events[-1]["routes"]] == KINDS and len(toy) == 1


def test_three_options_even_when_no_route_fits_the_preference_better(toy, monkeypatch):
    monkeypatch.setattr(model_client, "predict", lambda tiles, on_batch=None: [
        TileScore(h3_index=t.h3_index, scores=dict.fromkeys(DIMENSION_NAMES, 0.5)) for t in tiles])
    data = client.post("/route", json=BODY).json()
    assert [r["kind"] for r in data["routes"]] == KINDS and not data["improved"] and data["max_detour"] == 1.0
    assert len({json.dumps(r["geometry"]) for r in data["routes"]}) == 4


def test_only_the_cells_under_the_detour_ellipse_are_sent_to_the_model(toy):
    client.post("/route", json=BODY)
    assert sorted(toy[0] + toy[1]) == sorted(FAN_CELLS[:8]) and len(toy) == 2


def test_intensity_changes_the_recommendation_not_the_routes(toy, monkeypatch):
    strong = client.post("/route", json=BODY).json()
    monkeypatch.setattr(agent, "get_preference", lambda text: preference(0.3))
    weak = client.post("/route", json=BODY).json()
    assert weak["intensity"] == 0.3 and [r["geometry"] for r in weak["routes"]] == [r["geometry"] for r in strong["routes"]]
    assert (weak["recommended"], strong["recommended"]) == ("efficient", "preferred")


def test_without_text_nothing_is_parsed_or_predicted(toy, monkeypatch):
    monkeypatch.setattr(agent, "get_preference", lambda text: pytest.fail("Gemini must not be called"))
    monkeypatch.setattr(model_client, "predict", lambda tiles, on_batch=None: pytest.fail("the model must not be called"))
    data = client.post("/route", json={**BODY, "text": "  "}).json()
    assert [r["kind"] for r in data["routes"]] == ["shortest"] and data["recommended"] == "shortest" and data["weights"] == []


def test_cells_without_a_prediction_are_counted_and_still_give_three_options(toy, monkeypatch):
    monkeypatch.setattr(model_client, "predict", lambda tiles, on_batch=None: [TileScore(h3_index=t.h3_index, status="no_naip_coverage", scores=None)
                                                                                for t in tiles])
    data = client.post("/route", json=BODY).json()
    assert data["cells_without_score"] == data["cells_scored"] == 10 and not data["improved"]
    assert [r["kind"] for r in data["routes"]] == KINDS


def test_errors_map_to_http_statuses(toy, monkeypatch):
    far = {**BODY, "origin": {"lon": -70.0, "lat": 42.35}}
    assert client.post("/route", json=far).status_code == 422
    assert client.post("/route", json={**BODY, "origin": {"lon": 0, "lat": 91}}).status_code == 422
    for error, status in [(model_client.ModelTimeoutError("slow"), 504), (model_client.ModelServiceError("down"), 502)]:
        monkeypatch.setattr(model_client, "predict", lambda tiles, on_batch=None, error=error: (_ for _ in ()).throw(error))
        assert client.post("/route", json=BODY).status_code == status


def test_unknown_region_is_404():
    assert client.post("/route", json={**BODY, "region_id": "atlantis"}).status_code == 404
    assert client.post("/route", json={**BODY, "region_id": "../etc"}).status_code == 404
    assert client.post("/route/stream", json={**BODY, "region_id": "atlantis"}).status_code == 404
    assert client.post("/route/plan", json={k: BODY[k] for k in ("origin", "destination")} | {"region_id": "atlantis"}).status_code == 404


def test_boston_graph_plans_a_real_route(monkeypatch):
    """Smoke test with the graph shipped in data/graphs: Cambridge Common to Boston Common with synthetic scores."""
    def predict(tiles, on_batch=None):
        rng = random.Random(0)
        return [TileScore(h3_index=t.h3_index, scores={name: rng.random() for name in DIMENSION_NAMES}) for t in tiles]

    monkeypatch.setattr(agent, "get_preference", lambda text: preference())
    monkeypatch.setattr(model_client, "predict", predict)
    body = {"origin": {"lon": -71.1190, "lat": 42.3736}, "destination": {"lon": -71.0655, "lat": 42.3550}, "text": "shade"}
    response = client.post("/route", json=body)
    assert response.status_code == 200
    data = response.json()
    shortest = data["routes"][0]
    assert shortest["kind"] == "shortest" and 4000 < shortest["distance_m"] < 8000
    assert [r["kind"] for r in data["routes"]] == KINDS
    assert len({json.dumps(r["geometry"]) for r in data["routes"]}) == 4
    assert all(r["detour"] <= data["max_detour"] + 1e-6 for r in data["routes"]) and data["cells_scored"] > 50
    assert shortest["geometry"]["coordinates"][0][0] == pytest.approx(-71.1190, abs=0.01)    # (lon, lat) order


def test_plan_returns_the_shortest_route_and_the_cells_to_predict(toy):
    data = client.post("/route/plan", json={k: BODY[k] for k in ("origin", "destination")}).json()
    assert data["shortest"]["kind"] == "shortest" and data["shortest"]["distance_m"] == 8.0
    assert sorted(data["cells"]) == sorted(FAN_CELLS[:6]) and toy == []                  # nothing was predicted
    assert client.post("/route/plan", json={"origin": {"lon": 0, "lat": 0}, "destination": BODY["destination"]}).status_code == 422


def test_stream_colours_cells_batch_by_batch_then_sends_the_result(toy, monkeypatch):
    reference = np.full((20, len(DIMENSION_NAMES)), 0.5)
    reference[:, DIMENSION_NAMES.index("tree_canopy")] = np.linspace(0.0, 1.0, 20)
    monkeypatch.setattr(baseline, "load", lambda region_id: reference)
    events = stream_events(BODY)
    types = [e["type"] for e in events]
    assert types[0] == "start" and events[0]["cells_total"] == 6 and types[-1] == "result"
    assert types.index("preference") < types.index("cells")                      # colours need the preference first
    assert events[types.index("preference")]["intensity"] == 1.0
    batches = [e["values"] for e in events if e["type"] == "cells"]
    assert len(batches) == 4 and all(len(b) == 2 for b in batches)               # 3 batches, then the 2 cells of the widened limit
    match = {cell: value for batch in batches for cell, value in batch}
    assert len(match) == 8                                                       # every cell is coloured exactly once
    by_path = [match[FAN_CELLS[2 * k]] for k in range(4)]
    assert by_path == sorted(by_path) and by_path[0] == 0.0 and by_path[-1] > by_path[1]   # more canopy, better match
    assert events[-1]["area_mean"] == {"tree_canopy": 0.5}
    assert [r["kind"] for r in events[-1]["routes"]] == KINDS


def test_stream_without_an_area_baseline_sends_all_colours_once_at_the_end(toy):
    events = stream_events(BODY)
    batches = [e["values"] for e in events if e["type"] == "cells"]
    assert len(batches) == 1 and len(batches[0]) == 8 and events[-1]["type"] == "result" and events[-1]["area_mean"] == {}


def test_stream_reports_a_model_failure_as_an_event_and_input_errors_as_http_errors(toy, monkeypatch):
    monkeypatch.setattr(model_client, "predict", lambda tiles, on_batch=None: (_ for _ in ()).throw(model_client.ModelServiceError("down")))
    assert stream_events(BODY)[-1] == {"type": "error", "status": 502, "detail": "down"}
    assert client.post("/route/stream", json={**BODY, "origin": {"lon": -70.0, "lat": 42.35}}).status_code == 422


def test_candidates_show_the_length_match_tradeoff(toy):
    data = client.post("/route", json=BODY).json()
    candidates = data["candidates"]
    assert (candidates[0]["detour"], candidates[0]["kind"]) == (0.0, "shortest")
    assert {c["kind"] for c in candidates} >= set(KINDS)                                 # every offered route is listed
    best = next(c for c in candidates if c["kind"] == "preferred")
    assert best["match"] == next(r for r in data["routes"] if r["kind"] == "preferred")["match"]
