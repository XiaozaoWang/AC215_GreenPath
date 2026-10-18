import json
import sys
import subprocess
import numpy as np
import pandas as pd
import pytest
from greenpath_model.artifact import prepared_directory, seal, verify, write_json
from greenpath_model.bundle import RegressionBundle
from greenpath_model.predict import predict_file


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "model"
    with prepared_directory(root) as staging:
        write_json(staging / "model.json", {"model_version": "test-model", "dimensions": ["shade", "quietness"],
            "feature_columns": ["e0", "e1"], "score_range": [0, 1], "transform_dtype": "float32",
            "dataset_version": "test-data", "schema_version": "test-schema",
            "encoder": {"manifest_sha256": "expected-encoder"}})
        np.savez(staging / "weights.npz", mean=np.array([2., 4.]), scale=np.array([2., 2.]),
                 coef=np.array([[.2, .1], [-.1, .3]], dtype=np.float32), intercept=np.array([.5, .4], dtype=np.float32))
        seal(staging, "test-model", "greenpath-ridge-v1")
    return root


def test_reload_uses_scaling_and_feature_names_not_incoming_column_order(bundle):
    model = RegressionBundle(bundle)
    table = pd.DataFrame({"e1": [6., 400.], "cell_id": ["a", "b"], "e0": [4., 400.]})
    result = model.predict(table)
    np.testing.assert_allclose(result[0], [.8, .6], atol=1e-6)
    np.testing.assert_allclose(result[1], [1, 1])


@pytest.mark.parametrize("case", ["duplicate_id", "missing_feature", "extra_feature", "nan", "empty"])
def test_invalid_inference_inputs_are_rejected(bundle, case):
    table = pd.DataFrame({"cell_id": ["a", "b"], "e0": [1., 2.], "e1": [3., 4.]})
    if case == "duplicate_id": table.cell_id = "same"
    if case == "missing_feature": table = table.drop(columns="e1")
    if case == "extra_feature": table["e2"] = 0
    if case == "nan": table.loc[0, "e0"] = np.nan
    if case == "empty": table = table.iloc[:0]
    with pytest.raises(ValueError): RegressionBundle(bundle).predict(table)


def test_tampered_weights_fail_before_loading(bundle):
    (bundle / "weights.npz").write_bytes(b"not a model")
    with pytest.raises(ValueError, match="checksum"):
        RegressionBundle(bundle)


def test_nonpositive_scaler_is_rejected_even_with_valid_file_hash(bundle):
    with np.load(bundle / "weights.npz") as data:
        arrays = {key: data[key] for key in data.files}
    arrays["scale"][0] = 0
    np.savez(bundle / "weights.npz", **arrays)
    seal(bundle, "test-model", "greenpath-ridge-v1")
    with pytest.raises(ValueError, match="numeric"):
        RegressionBundle(bundle)


def test_prediction_output_is_immutable_and_records_keep_ids(bundle, tmp_path):
    inp = tmp_path / "input.parquet"
    pd.DataFrame({"cell_id": ["b", "a"], "e0": [2., 4.], "e1": [4., 6.]}).to_parquet(inp)
    out = tmp_path / "out"
    result = predict_file(bundle, inp, out, "pred-v1")
    assert result["n_cells"] == 2
    verify(out)
    assert [r["cell_id"] for r in json.loads((out / "scores.json").read_text())] == ["b", "a"]
    with pytest.raises(ValueError, match="overwrite"):
        predict_file(bundle, inp, out, "pred-v1")


def test_failed_artifact_creation_never_exposes_final_directory(tmp_path):
    dest = tmp_path / "failed"
    with pytest.raises(RuntimeError):
        with prepared_directory(dest) as staging:
            (staging / "partial").write_text("incomplete")
            raise RuntimeError("simulated interruption")
    assert not dest.exists() and not list(tmp_path.glob(".preparing-*"))


def test_same_shape_from_a_different_encoder_is_rejected(bundle, tmp_path):
    root = tmp_path / "embeddings"
    with prepared_directory(root) as staging:
        pd.DataFrame({"cell_id": ["a"], "e0": [2.], "e1": [4.]}).to_parquet(staging / "embeddings.parquet")
        write_json(staging / "embedding.json", {"encoder_manifest_sha256": "different-encoder"})
        seal(staging, "wrong-embeddings", "greenpath-embeddings-v1")
    with pytest.raises(ValueError, match="Encoder provenance"):
        predict_file(bundle, root / "embeddings.parquet", tmp_path / "out", "bad-prediction")


def test_inference_package_does_not_import_training_or_encoder():
    code = "import sys; import greenpath_model.predict; assert 'torch' not in sys.modules and 'sklearn' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
