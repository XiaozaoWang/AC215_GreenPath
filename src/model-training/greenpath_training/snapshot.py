"""Prepare only the frozen inputs required to reproduce the selected M1 refit."""
import argparse
import json
from pathlib import Path
import shutil
from greenpath_model.artifact import digest, prepared_directory, safe_path, seal, write_json


def prepare(data_root, experiment, output, artifact_version):
    data_root = Path(data_root)
    config_rel = f"results/{experiment}/config.json"
    config = json.loads(safe_path(data_root, config_rel).read_text())
    version = config["dataset_version"]
    if config["chosen"]["best_backbone"] != "clip_b16" or config["tta"] is not True:
        raise ValueError("Only the selected eight-view CLIP M1 inputs are supported")
    paths = [f"datasets/{version}/dataset.parquet", f"datasets/{version}/schema.json",
        f"embeddings/{version}/clip_b16_tta.parquet", config_rel, f"results/{experiment}/predictions.parquet"]
    with prepared_directory(output) as staging:
        source_hashes = {}
        for name in paths:
            source = safe_path(data_root, name)
            dest = safe_path(staging, name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            source_hashes[name] = digest(source)
        schema_path = staging / f"datasets/{version}/schema.json"
        schema = json.loads(schema_path.read_text())
        schema["notes"] = "Targets are in [0,1]; missing labels are NaN. tile_path is relative to the data root. fold is defined for train/val only."
        write_json(schema_path, schema)
        write_json(staging / "snapshot.json", {"dataset_version": version, "experiment": experiment,
            "scope": "Selected M1 refit inputs only; no raw images, teacher calls or other backbone caches.",
            "original_source_sha256": source_hashes,
            "metadata_translation": "Only dataset schema notes are translated into English; original source hashes are retained. All numeric tables are byte-for-byte copies.",
            "training_config": config_rel, "label_validation": "Teacher-generated labels; human validation pending."})
        seal(staging, artifact_version, "greenpath-m1-training-inputs-v1")
    return {"artifact_version": artifact_version, "dataset_version": version, "training_config": config_rel}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root", type=Path, required=True)
    p.add_argument("--experiment", default="baselines-first-run-20261013T0242")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--artifact-version", required=True)
    a = p.parse_args()
    print(json.dumps(prepare(a.data_root, a.experiment, a.output, a.artifact_version)))


if __name__ == "__main__":
    main()
