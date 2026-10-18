"""Refit the preselected M1 model on train+val and export portable numeric state."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from greenpath_model.artifact import digest, prepared_directory, safe_path, seal, verify, write_json
from greenpath_model.bundle import RegressionBundle


def load_inputs(data_root, config_file, contract_file, embedding_file=None):
    data_root = Path(data_root)
    config = json.loads(Path(config_file).read_text())
    contract = json.loads(Path(contract_file).read_text())
    version = config["dataset_version"]
    schema_file = data_root / "datasets" / version / "schema.json"
    schema = json.loads(schema_file.read_text())
    targets = schema["targets"]
    if targets != list(contract["dimensions"]) or contract["score_range"] != [0, 1]:
        raise ValueError("Dataset targets and current [0,1] application contract must match")
    dataset_file = schema_file.with_name("dataset.parquet")
    dataset = pd.read_parquet(dataset_file).sort_values("cell_id").reset_index(drop=True)
    if dataset.empty or dataset.cell_id.duplicated().any() or not dataset.cell_id.map(lambda x: isinstance(x, str) and bool(x)).all():
        raise ValueError("Dataset IDs must be nonempty and unique")
    if not set(dataset.split).issubset({"train", "val", "test"}) or not {"train", "val", "test"}.issubset(set(dataset.split)):
        raise ValueError("Expected explicit train, val and test splits")
    if set(dataset["dataset_version"].dropna().astype(str)) != {str(version)}:
        raise ValueError("Dataset version column does not match selected version")
    # Catch spatial leakage rather than assuming the historical split was preserved.
    if dataset.groupby("block_id").split.nunique().max() > 1:
        raise ValueError("A spatial block appears in multiple dataset splits")
    backbone = config["chosen"]["best_backbone"]
    if backbone != "clip_b16" or config["tta"] is not True:
        raise ValueError("This initial export supports the selected CLIP-B/16 eight-view M1 only")
    alpha = config["chosen"][f"M1_{backbone}"]["alpha"]
    if not np.isfinite(alpha) or alpha <= 0:
        raise ValueError("Ridge alpha must be positive")
    embedding_file = Path(embedding_file) if embedding_file is not None else data_root / "embeddings" / version / f"{backbone}_tta.parquet"
    embeddings = pd.read_parquet(embedding_file)
    if embeddings.cell_id.duplicated().any() or set(embeddings.cell_id) != set(dataset.cell_id):
        raise ValueError("Embeddings and dataset IDs must match exactly")
    feature_names = [f"e{i}" for i in range(config["embedding_meta"][backbone]["dim"])]
    if set(embeddings.columns) != {"cell_id", *feature_names}:
        raise ValueError("Unexpected embedding feature columns")
    embeddings = embeddings.set_index("cell_id").loc[dataset.cell_id, feature_names]
    x = embeddings.to_numpy(dtype=np.float32)
    y = dataset[targets].to_numpy(dtype=np.float64)
    if not np.isfinite(x).all() or np.isinf(y).any() or np.any((y < 0) | (y > 1)):
        raise ValueError("Invalid embedding values or labels outside [0,1]")
    pool = dataset.split.isin(["train", "val"]).to_numpy()
    test = (dataset.split == "test").to_numpy()
    if pool.sum() != config["n_pool"] or test.sum() != config["n_test"]:
        raise ValueError("Frozen dataset counts differ from the selected experiment")
    return config, contract, dataset, x, y, pool, test, feature_names, [dataset_file, schema_file, embedding_file, Path(config_file), Path(contract_file)]


def fit_heads(x, y, pool, alpha):
    """Fit preprocessing only on train+val; each target ignores its missing labels."""
    scaler = StandardScaler().fit(x[pool])
    transformed = scaler.transform(x[pool])
    heads = []
    for j in range(y.shape[1]):
        valid = ~np.isnan(y[pool, j])
        if valid.sum() < 2:
            raise ValueError(f"Target {j} has fewer than two training labels")
        heads.append(Ridge(alpha=alpha).fit(transformed[valid], y[pool, j][valid]))
    return scaler, heads


def evaluate(y, prediction, targets):
    result = {}
    for j, target in enumerate(targets):
        valid = np.isfinite(y[:, j])
        truth, pred = y[valid, j], prediction[valid, j]
        result[target] = {"n": int(valid.sum()),
            "mae": float(np.mean(np.abs(truth - pred))) if len(truth) else None,
            "rmse": float(np.sqrt(np.mean((truth - pred) ** 2))) if len(truth) else None,
            "spearman": float(spearmanr(truth, pred).statistic) if len(truth) >= 5 and np.ptp(truth) and np.ptp(pred) else 0.0}
    return result


def train(data_root, config_file, contract_file, encoder_dir, output, artifact_version, tolerance=1e-5, embedding_file=None, reference_policy="required"):
    if not np.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Validation tolerance must be finite and nonnegative")
    if reference_policy not in ["required", "none"]:
        raise ValueError("Reference policy must be required or none")
    config, contract, ds, x, y, pool, test, features, inputs = load_inputs(data_root, config_file, contract_file, embedding_file)
    encoder_dir = Path(encoder_dir)
    enc_manifest = verify(encoder_dir, "greenpath-clip-encoder-v1")
    if "encoder.json" not in enc_manifest["files"]:
        raise ValueError("Encoder metadata must be checksummed")
    encoder = json.loads((encoder_dir / "encoder.json").read_text())
    weights_name = encoder["weights_file"]
    if weights_name not in enc_manifest["files"] or digest(safe_path(encoder_dir, weights_name)) != encoder["weights_sha256"]:
        raise ValueError("Encoder weights and metadata do not agree")
    if encoder["source"] != config["embedding_meta"]["clip_b16"]["source"] or encoder["output_dim"] != len(features) or not encoder["tta"]:
        raise ValueError("Encoder does not match the historical embedding specification")
    if embedding_file is not None:
        embedding_root = Path(embedding_file).parent
        embedding_manifest = verify(embedding_root, "greenpath-embeddings-v1")
        if not {Path(embedding_file).name, "embedding.json"}.issubset(embedding_manifest["files"]):
            raise ValueError("Fresh embedding data and provenance must be checksummed")
        embedding_info = json.loads((embedding_root / "embedding.json").read_text())
        if embedding_info["encoder_manifest_sha256"] != digest(encoder_dir / "manifest.json"):
            raise ValueError("Training embeddings belong to another encoder")
    alpha = config["chosen"]["M1_clip_b16"]["alpha"]
    scaler, heads = fit_heads(x, y, pool, alpha)
    test_x = scaler.transform(x[test])
    before = np.clip(np.column_stack([head.predict(test_x) for head in heads]), 0, 1)
    reference_error = None
    if reference_policy == "required":
        source_predictions = Path(config_file).with_name("predictions.parquet")
        ref = pd.read_parquet(source_predictions)
        ref = ref[ref.split == "test"]
        if ref.cell_id.duplicated().any() or set(ref.cell_id) != set(ds.loc[test, "cell_id"]):
            raise ValueError("Reference test IDs differ from the frozen dataset")
        ref = ref.set_index("cell_id").loc[ds.loc[test, "cell_id"], [f"M1__{d}" for d in contract["dimensions"]]].to_numpy()
        if not np.isfinite(ref).all():
            raise ValueError("Reference predictions contain nonfinite values")
        reference_error = float(np.max(np.abs(before - ref)))
        if reference_error > tolerance:
            raise ValueError(f"Refit differs from historical predictions: max_abs={reference_error:g}, tolerance={tolerance:g}; investigate before publishing")
        inputs.append(source_predictions)
    targets = list(contract["dimensions"])
    metadata = {"model_version": artifact_version, "model_type": "standard_scaler_independent_ridge",
        "dataset_version": config["dataset_version"], "schema_version": contract["schema_version"],
        "dimensions": targets, "score_range": contract["score_range"], "feature_columns": features,
        "transform_dtype": "float32", "encoder": {"artifact_version": enc_manifest["artifact_version"],
            "manifest_sha256": digest(encoder_dir / "manifest.json"), "preprocessing": encoder["preprocessing"],
            "weights_sha256": encoder["weights_sha256"], "source_revision": encoder["source_revision"]},
        "training": {"alpha": alpha, "fit_splits": ["train", "val"], "n_fit": int(pool.sum()),
            "n_test": int(test.sum()), "selection": "Reuse the prior spatial-CV choice; no retuning or test-driven selection.",
            "source_experiment": config.get("selection_source_experiment", config["run_id"]), "reference_policy": reference_policy},
        "versions": {name: importlib.metadata.version(name) for name in ["numpy", "pandas", "scikit-learn", "scipy"]},
        "python": platform.python_version(), "label_validation": "Teacher-generated labels; human validation pending.",
        "source_files": {str(p.relative_to(data_root)) if p.is_relative_to(data_root) else p.name: digest(p) for p in inputs},
        "scope": "Reloadable M1 regression state; image encoding uses the separately versioned CLIP artifact."}
    with prepared_directory(output) as staging:
        write_json(staging / "model.json", metadata)
        np.savez(staging / "weights.npz", mean=scaler.mean_, scale=scaler.scale_,
                 coef=np.stack([h.coef_ for h in heads]), intercept=np.asarray([h.intercept_ for h in heads]))
        write_json(staging / "split-ids.json", {"fit": ds.loc[pool, "cell_id"].tolist(), "test": ds.loc[test, "cell_id"].tolist()})
        write_json(staging / "metrics.json", {"split": "held_out_test", "use": "Reproduction report only, not hyperparameter selection or serving error calibration.",
            "per_dimension": evaluate(y[test], before, targets)})
        validation = {"reference_max_abs_error": reference_error, "reference_tolerance": tolerance if reference_policy == "required" else None,
            "reference_policy": reference_policy,
            "n_test": int(test.sum()), "no_test_rows_used_for_fit": True}
        write_json(staging / "validation.json", validation)
        seal(staging, artifact_version, "greenpath-ridge-v1")
        model = RegressionBundle(staging)
        table = pd.DataFrame(x[test], columns=features); table.insert(0, "cell_id", ds.loc[test, "cell_id"].values)
        loaded = model.predict(table)
        reload_error = float(np.max(np.abs(before - loaded)))
        if reload_error > 1e-6:
            raise ValueError(f"Reloaded model differs from fitted state: {reload_error:g}")
        # Reload in an entirely separate process without any fitted objects in memory.
        with tempfile.TemporaryDirectory() as temp:
            inp, out = Path(temp) / "embeddings.parquet", Path(temp) / "predictions"
            table.to_parquet(inp, index=False)
            subprocess.run([sys.executable, "-m", "greenpath_model.predict", "--model-dir", str(staging),
                "--embeddings", str(inp), "--output", str(out), "--artifact-version", "reload-check"], check=True, capture_output=True, text=True)
            records = json.loads((out / "scores.json").read_text())
            fresh = np.asarray([[row["scores"][d] for d in targets] for row in records])
            fresh_error = float(np.max(np.abs(before - fresh)))
            if fresh_error > 1e-6:
                raise ValueError("Fresh-process reload differs from fitted state")
        validation.update({"reload_max_abs_error": reload_error, "fresh_process_max_abs_error": fresh_error,
                           "reload_tolerance": 1e-6, "fresh_process_verified": True})
        write_json(staging / "validation.json", validation)
        seal(staging, artifact_version, "greenpath-ridge-v1")
    return {"model_version": artifact_version, **validation}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ["data-root", "config", "contract", "encoder-dir", "output"]:
        p.add_argument(f"--{name}", type=Path, required=True)
    p.add_argument("--artifact-version", required=True)
    p.add_argument("--embeddings", type=Path)
    p.add_argument("--reference-policy", choices=["required", "none"], default="required")
    a = p.parse_args()
    print(json.dumps(train(a.data_root.resolve(), a.config.resolve(), a.contract.resolve(), a.encoder_dir.resolve(), a.output.resolve(), a.artifact_version,
        embedding_file=a.embeddings.resolve() if a.embeddings else None, reference_policy=a.reference_policy)))


if __name__ == "__main__":
    main()
