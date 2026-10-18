import json
import pytest
from greenpath_model.artifact import digest, seal, write_json, verify
from greenpath_model.region import package_region


@pytest.fixture
def region_inputs(tmp_path):
    model = tmp_path / "model"; model.mkdir()
    write_json(model / "model.json", {"model_version": "test-model", "schema_version": "test-schema", "dimensions": ["shade"],
        "dataset_version": "test-data", "encoder": {"artifact_version": "test-encoder"}})
    seal(model, "test-model", "greenpath-ridge-v1")
    prepared = tmp_path / "prepared"; prepared.mkdir()
    write_json(prepared / "schema.json", {"schema_version": "test-schema", "score_range": [0, 1], "dimensions": {"shade": {"description": "Shade"}}})
    write_json(prepared / "preparation.json", {"dataset_version": "test-data", "source_version": "test-source", "mode": "smoke", "counts": {"train": 1, "val": 0, "test": 0}})
    write_json(prepared / "region.geojson", {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "a", "properties": {"cell_id": "a", "h3_index": "example"},
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}]})
    seal(prepared, "test-prepared", "greenpath-prepared-data-v1")
    predictions = tmp_path / "predictions"; predictions.mkdir()
    write_json(predictions / "prediction.json", {"model_version": "test-model", "model_manifest_sha256": digest(model / "manifest.json")})
    write_json(predictions / "scores.json", [{"cell_id": "a", "scores": {"shade": .5}}])
    seal(predictions, "test-predictions", "greenpath-predictions-v1")
    return prepared, predictions, model


def test_region_package_keeps_geometry_and_model_provenance(region_inputs, tmp_path):
    out = tmp_path / "serving"
    result = package_region(*region_inputs, out, "serving-test")
    assert result["n_cells"] == 1
    m = verify(out, "greenpath-region-v1")
    assert m["prediction_source"] == "batch_inference_reloaded_model" and m["rmse"] is None
    assert (out / "region.geojson").read_bytes() == (region_inputs[0] / "region.geojson").read_bytes()


@pytest.mark.parametrize("case", ["wrong_model", "missing_id", "duplicate_id", "wrong_schema"])
def test_region_rejects_misaligned_inputs(region_inputs, tmp_path, case):
    prepared, predictions, model = region_inputs
    if case == "wrong_model":
        info = json.loads((predictions / "prediction.json").read_text()); info["model_manifest_sha256"] = "different"
        write_json(predictions / "prediction.json", info)
    if case in ["missing_id", "duplicate_id"]:
        records = [{"cell_id": "different", "scores": {"shade": .5}}] if case == "missing_id" else [{"cell_id": "a", "scores": {"shade": .5}}] * 2
        write_json(predictions / "scores.json", records)
    if case == "wrong_schema":
        schema = json.loads((prepared / "schema.json").read_text()); schema["schema_version"] = "different"
        write_json(prepared / "schema.json", schema); seal(prepared, "test-prepared", "greenpath-prepared-data-v1")
    seal(predictions, "test-predictions", "greenpath-predictions-v1")
    with pytest.raises(ValueError): package_region(prepared, predictions, model, tmp_path / "bad", "bad")
    assert not (tmp_path / "bad").exists()
