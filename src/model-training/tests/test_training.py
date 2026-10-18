import json
import numpy as np
import pandas as pd
import pytest
from greenpath_model.artifact import digest, seal, write_json
from greenpath_training.train import fit_heads, load_inputs, train
from greenpath_training.snapshot import prepare
from greenpath_model.artifact import verify


def test_scaler_never_observes_test_values_and_targets_ignore_missing_labels():
    x = np.array([[1, 2], [2, 3], [3, 4], [1e8, 1e8]], dtype=np.float32)
    y = np.array([[.1, .2], [.3, np.nan], [.5, .6], [.9, .9]])
    scaler, heads = fit_heads(x, y, np.array([True, True, True, False]), 10)
    np.testing.assert_allclose(scaler.mean_, [2, 3])
    assert len(heads) == 2 and np.isfinite(heads[1].coef_).all()


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "data"
    dataset_dir = root / "datasets/v1"; dataset_dir.mkdir(parents=True)
    emb_dir = root / "embeddings/v1"; emb_dir.mkdir(parents=True)
    run = root / "results/selected"; run.mkdir(parents=True)
    ids = [f"cell-{i}" for i in range(10)]
    ds = pd.DataFrame({"cell_id": ids, "dataset_version": "v1", "split": ["train"] * 5 + ["val"] * 3 + ["test"] * 2,
                       "block_id": ids, "shade": np.linspace(.1, .9, 10), "quietness": np.linspace(.8, .2, 10)})
    ds.to_parquet(dataset_dir / "dataset.parquet", index=False)
    write_json(dataset_dir / "schema.json", {"targets": ["shade", "quietness"]})
    emb = pd.DataFrame(np.random.default_rng(42).normal(size=(10, 3)).astype(np.float32), columns=["e0", "e1", "e2"])
    emb.insert(0, "cell_id", ids); emb.to_parquet(emb_dir / "clip_b16_tta.parquet", index=False)
    config = {"run_id": "selected", "dataset_version": "v1", "tta": True, "chosen": {"best_backbone": "clip_b16", "M1_clip_b16": {"alpha": 10}},
              "embedding_meta": {"clip_b16": {"dim": 3, "source": "test-source"}}, "n_pool": 8, "n_test": 2}
    write_json(run / "config.json", config)
    contract = tmp_path / "contract.json"; write_json(contract, {"schema_version": "test-schema", "score_range": [0, 1], "dimensions": {"shade": {}, "quietness": {}}})
    encoder = tmp_path / "encoder"; encoder.mkdir()
    (encoder / "synthetic-weights.bin").write_bytes(b"synthetic weights; encoder execution is tested separately")
    write_json(encoder / "encoder.json", {"source": "test-source", "output_dim": 3, "tta": True,
               "preprocessing": {}, "weights_file": "synthetic-weights.bin", "weights_sha256": digest(encoder / "synthetic-weights.bin"), "source_revision": "test-revision"})
    seal(encoder, "test-encoder", "greenpath-clip-encoder-v1")
    x = emb[["e0", "e1", "e2"]].to_numpy(); y = ds[["shade", "quietness"]].to_numpy(); pool = ds.split.isin(["train", "val"]).to_numpy()
    scaler, heads = fit_heads(x, y, pool, 10)
    ref = np.clip(np.column_stack([head.predict(scaler.transform(x[~pool])) for head in heads]), 0, 1)
    pd.DataFrame({"cell_id": ids[-2:], "split": "test", "M1__shade": ref[:, 0], "M1__quietness": ref[:, 1]}).to_parquet(run / "predictions.parquet", index=False)
    return root, run / "config.json", contract, encoder


def test_complete_export_and_fresh_process_reproduction(inputs, tmp_path):
    result = train(*inputs, tmp_path / "model", "synthetic-model-v1")
    assert result["fresh_process_verified"] and result["reference_max_abs_error"] <= 1e-5
    assert result["fresh_process_max_abs_error"] <= 1e-6


@pytest.mark.parametrize("case", ["extra_embedding_id", "duplicate_embedding_id", "target_order", "spatial_leakage"])
def test_inconsistent_training_inputs_fail(inputs, case):
    root, config, contract, encoder = inputs
    if case in {"extra_embedding_id", "duplicate_embedding_id"}:
        p = root / "embeddings/v1/clip_b16_tta.parquet"; e = pd.read_parquet(p)
        e.loc[0, "cell_id"] = "missing" if case == "extra_embedding_id" else e.loc[1, "cell_id"]
        e.to_parquet(p, index=False)
    if case == "target_order":
        c = json.loads(contract.read_text()); c["dimensions"] = dict(reversed(list(c["dimensions"].items()))); write_json(contract, c)
    if case == "spatial_leakage":
        p = root / "datasets/v1/dataset.parquet"; d = pd.read_parquet(p); d.loc[9, "block_id"] = d.loc[0, "block_id"]; d.to_parquet(p, index=False)
    with pytest.raises(ValueError): load_inputs(root, config, contract)


def test_refit_mismatch_is_not_published(inputs, tmp_path):
    root, config, contract, encoder = inputs
    p = config.with_name("predictions.parquet"); frame = pd.read_parquet(p); frame["M1__shade"] = 0; frame.to_parquet(p, index=False)
    out = tmp_path / "model"
    with pytest.raises(ValueError, match="Refit differs"):
        train(root, config, contract, encoder, out, "bad-model")
    assert not out.exists()


def test_minimal_training_snapshot_preserves_tables_and_can_be_refit(inputs, tmp_path):
    root, config, contract, encoder = inputs
    out = tmp_path / "snapshot"
    prepare(root, "selected", out, "test-inputs")
    verify(out, "greenpath-m1-training-inputs-v1")
    for rel in ["datasets/v1/dataset.parquet", "embeddings/v1/clip_b16_tta.parquet", "results/selected/predictions.parquet"]:
        assert (out / rel).read_bytes() == (root / rel).read_bytes()
    result = train(out, out / "results/selected/config.json", contract, encoder, tmp_path / "from-snapshot", "snapshot-refit")
    assert result["fresh_process_verified"]
    with pytest.raises(ValueError, match="overwrite"):
        prepare(root, "selected", out, "test-inputs")


def test_fresh_pipeline_embeddings_need_matching_provenance_but_no_old_predictions(inputs, tmp_path):
    import shutil
    root, config, contract, encoder = inputs
    fresh = tmp_path / "fresh-embeddings"; fresh.mkdir()
    shutil.copyfile(root / "embeddings/v1/clip_b16_tta.parquet", fresh / "embeddings.parquet")
    write_json(fresh / "embedding.json", {"encoder_manifest_sha256": digest(encoder / "manifest.json")})
    seal(fresh, "fresh-test-embeddings", "greenpath-embeddings-v1")
    config.with_name("predictions.parquet").unlink()
    result = train(root, config, contract, encoder, tmp_path / "fresh-model", "fresh-model",
                   embedding_file=fresh / "embeddings.parquet", reference_policy="none")
    assert result["reference_max_abs_error"] is None and result["fresh_process_verified"]
    write_json(fresh / "embedding.json", {"encoder_manifest_sha256": "wrong-encoder"})
    seal(fresh, "different-embeddings", "greenpath-embeddings-v1")
    with pytest.raises(ValueError, match="another encoder"):
        train(root, config, contract, encoder, tmp_path / "bad-model", "bad-model",
              embedding_file=fresh / "embeddings.parquet", reference_policy="none")
