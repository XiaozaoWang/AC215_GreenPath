import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api import model_client, pipeline, prompts, tiles
from api.dimensions import DIMENSION_NAMES
from api.schemas import AgentResult, Preference, ScoreRequest, Tile, TileScore
from api.service import app

# Independent copy of the handoff targets catches accidental schema changes.
TARGETS = "tree_canopy forest_environment grassland open_landscape road_width water_access urban_density street_enclosure farmland_pastoral industrial_area historic_character commercial_district scenic_appeal architectural_character visual_clutter".split()


def test_dimension_contract():
    assert DIMENSION_NAMES == TARGETS
    assert ScoreRequest(text="ride").region_id == "city_ma_boston_big"
    assert "hilly_terrain" not in prompts.SYSTEM_INSTRUCTION
    with pytest.raises(ValidationError):
        Preference(must_have=["hilly_terrain"])
    for scores in [dict.fromkeys(TARGETS[:-1], 0.5), dict.fromkeys(TARGETS + ["hilly_terrain"], 0.5),
                   dict.fromkeys(TARGETS, float("nan"))]:
        with pytest.raises(ValidationError):
            TileScore(h3_index="a", scores=scores)
    for status in ["not_downloaded", "no_naip_coverage", "mostly_black"]:
        assert TileScore(h3_index="a", status=status, scores=None).scores is None
        with pytest.raises(ValidationError):
            TileScore(h3_index="a", status=status, scores=dict.fromkeys(TARGETS, 0.5))
    with pytest.raises(ValidationError):
        TileScore(h3_index="a", scores=None)


def test_pipeline_keeps_null_cells_out_of_statistics(monkeypatch):
    predictions = [TileScore(h3_index="a", scores=dict.fromkeys(TARGETS, 0.2)),
                   TileScore(h3_index="b", status="mostly_black", scores=None),
                   TileScore(h3_index="c", scores=dict.fromkeys(TARGETS, 0.8))]
    monkeypatch.setattr(tiles, "get_tiles", lambda _: [Tile(h3_index=p.h3_index) for p in predictions])
    monkeypatch.setattr(model_client, "predict", lambda _: predictions)
    preference = AgentResult(preference=Preference(), attempts=[], used_fallback=False)
    for rows in [predictions, [predictions[1]]]:
        monkeypatch.setattr(model_client, "predict", lambda _, rows=rows: rows)
        result = pipeline.run_score("ride", "city_ma_boston_big", get_preference=lambda _: preference)
        assert result.cells[rows.index(predictions[1])].value is None
        assert result.stats.n_excluded == 1
        assert result.stats.mean == (dict.fromkeys(TARGETS, 0.5) if len(rows) > 1 else {})


def test_region_and_routing_contracts(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(tiles, "get_tiles", lambda _: [Tile(h3_index="a")])
    assert client.get("/regions/city_ma_boston_big").json() == {"region_id": "city_ma_boston_big", "h3_index": ["a"]}
    assert client.post("/route", json={}).status_code == 422
    assert client.post("/route", json={"origin": {"lat": 42.36, "lon": -71.06},
                                      "destination": {"lat": 42.37, "lon": -71.07}}).status_code == 501


@pytest.mark.parametrize("error,status", [(tiles.UnknownRegionError("missing"), 404),
    (tiles.RegionLoadError("GCS failed"), 503), (model_client.ModelServiceError("model failed"), 502),
    (model_client.ModelTimeoutError("timeout"), 504)])
def test_score_errors(monkeypatch, error, status):
    def fail(*args):
        raise error
    monkeypatch.setattr(pipeline, "run_score", fail)
    assert TestClient(app).post("/score", json={"text": "ride"}).status_code == status
