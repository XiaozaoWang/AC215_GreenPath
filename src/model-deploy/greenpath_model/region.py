"""Connect freshly computed scores to the exact prepared H3 region for the API."""
import argparse
import json
from pathlib import Path
import shutil
from greenpath_model.artifact import digest, prepared_directory, seal, verify, write_json
from greenpath_model.predict import predict_file


def package_region(prepared_dir, predictions_dir, model_dir, output, artifact_version):
    prepared_dir, predictions_dir, model_dir = map(Path, [prepared_dir, predictions_dir, model_dir])
    verify(prepared_dir, "greenpath-prepared-data-v1")
    verify(predictions_dir, "greenpath-predictions-v1")
    verify(model_dir, "greenpath-ridge-v1")
    model = json.loads((model_dir / "model.json").read_text())
    prediction = json.loads((predictions_dir / "prediction.json").read_text())
    preparation = json.loads((prepared_dir / "preparation.json").read_text())
    schema = json.loads((prepared_dir / "schema.json").read_text())
    if prediction["model_manifest_sha256"] != digest(model_dir / "manifest.json") or prediction["model_version"] != model["model_version"]:
        raise ValueError("Predictions belong to a different model")
    if model["schema_version"] != schema["schema_version"] or model["dimensions"] != list(schema["dimensions"]) or model["dataset_version"] != preparation["dataset_version"]:
        raise ValueError("Prepared region and model contracts differ")
    geometry = json.loads((prepared_dir / "region.geojson").read_text())
    records = json.loads((predictions_dir / "scores.json").read_text())
    features = geometry.get("features", [])
    if geometry.get("type") != "FeatureCollection" or not features or not records:
        raise ValueError("Region and predictions must be nonempty")
    ids = [f["properties"]["cell_id"] for f in features]
    score_ids = [r["cell_id"] for r in records]
    if len(ids) != len(set(ids)) or len(score_ids) != len(set(score_ids)) or set(ids) != set(score_ids):
        raise ValueError("Region and prediction IDs must match one to one")
    if any(f.get("id") != f["properties"]["cell_id"] or f["geometry"]["type"] != "Polygon" for f in features):
        raise ValueError("Region features need stable IDs and Polygon geometry")
    mode = preparation["mode"]
    with prepared_directory(output) as staging:
        for name, source in [("schema.json", prepared_dir / "schema.json"), ("region.geojson", prepared_dir / "region.geojson"), ("scores.json", predictions_dir / "scores.json")]:
            shutil.copyfile(source, staging / name)
        write_json(staging / "provenance.json", {"model_manifest_sha256": digest(model_dir / "manifest.json"),
            "prediction_manifest_sha256": digest(predictions_dir / "manifest.json"),
            "prepared_manifest_sha256": digest(prepared_dir / "manifest.json"),
            "source_version": preparation["source_version"], "dataset_version": preparation["dataset_version"],
            "model_version": model["model_version"], "encoder_version": model["encoder"]["artifact_version"],
            "mode": mode, "counts": preparation["counts"],
            "scope": "Fresh image encoding, model fit and reload prediction in this run; no scores copied from historical experiments.",
            "evaluation_note": "Smoke runs validate execution only. Displayed regions include fitting and test cells; map appearance is not an independent accuracy evaluation."})
        seal(staging, artifact_version, "greenpath-region-v1")
        m = json.loads((staging / "manifest.json").read_text())
        m.update({"schema_version": schema["schema_version"], "schema_file": "schema.json",
            "model_version": model["model_version"], "dataset_version": model["dataset_version"],
            "region_id": artifact_version, "region_name": f"Boston H3 pipeline {mode} ({len(ids)} cells)",
            "prediction_source": "batch_inference_reloaded_model", "description": f"Fresh container pipeline predictions over {len(ids)} {mode} cells; not per-request image inference.",
            "geometry_file": "region.geojson", "scores_file": "scores.json",
            "model_output": {"format": "records", "id_field": "cell_id", "scores_field": "scores"}, "rmse": None})
        write_json(staging / "manifest.json", m)
    return {"artifact_version": artifact_version, "n_cells": len(ids), "model_version": model["model_version"], "mode": mode}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ["model-dir", "embeddings", "prepared-dir", "output", "serving-output"]:
        p.add_argument(f"--{name}", type=Path, required=True)
    p.add_argument("--artifact-version", required=True)
    p.add_argument("--serving-version", required=True)
    a = p.parse_args()
    prediction = predict_file(a.model_dir, a.embeddings, a.output, a.artifact_version)
    region = package_region(a.prepared_dir, a.output, a.model_dir, a.serving_output, a.serving_version)
    print(json.dumps({"prediction": prediction, "serving": region}), flush=True)


if __name__ == "__main__":
    main()
