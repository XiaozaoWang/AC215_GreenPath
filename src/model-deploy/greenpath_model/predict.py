"""Independent batch prediction from a verified regression bundle and embeddings."""
import argparse
import json
from pathlib import Path
import pandas as pd
from greenpath_model.artifact import digest, prepared_directory, seal, write_json, verify
from greenpath_model.bundle import RegressionBundle


def predict_file(model_directory, embedding_file, output, artifact_version):
    model = RegressionBundle(model_directory)
    embedding_file = Path(embedding_file)
    # Newly generated embedding artifacts carry an encoder identity. Historical notebook
    # caches have no such manifest and are explicitly identified as legacy inputs below.
    parent = embedding_file.parent
    input_manifest = None
    if (parent / "manifest.json").exists():
        input_manifest = verify(parent, "greenpath-embeddings-v1")
        if embedding_file.name not in input_manifest["files"]:
            raise ValueError("Embedding input is not covered by its manifest")
        if "embedding.json" not in input_manifest["files"]:
            raise ValueError("Embedding provenance must be checksummed")
        info = json.loads((parent / "embedding.json").read_text())
        if info["encoder_manifest_sha256"] != model.metadata["encoder"]["manifest_sha256"]:
            raise ValueError("Encoder provenance does not match the regression model")
    table = pd.read_parquet(embedding_file)
    values = model.predict(table)
    rows = [{"cell_id": key, "scores": dict(zip(model.dimensions, map(float, row)))}
            for key, row in zip(table.cell_id, values)]
    with prepared_directory(output) as staging:
        write_json(staging / "scores.json", rows)
        write_json(staging / "prediction.json", {"model_version": model.metadata["model_version"],
                   "model_manifest_sha256": digest(model.root / "manifest.json"),
                   "dataset_version": model.metadata["dataset_version"],
                   "schema_version": model.metadata["schema_version"],
                   "dimensions": model.dimensions, "n_cells": len(rows),
                   "input_sha256": digest(embedding_file),
                   "input_provenance": "versioned_encoder_artifact" if input_manifest else "historical_notebook_embedding_cache",
                   "prediction_source": "reloaded_regression_model"})
        seal(staging, artifact_version, "greenpath-predictions-v1")
    return {"artifact_version": artifact_version, "n_cells": len(rows), "model_version": model.metadata["model_version"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model-dir", type=Path, required=True)
    p.add_argument("--embeddings", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--artifact-version", required=True)
    a = p.parse_args()
    print(json.dumps(predict_file(a.model_dir, a.embeddings, a.output, a.artifact_version)))


if __name__ == "__main__":
    main()
