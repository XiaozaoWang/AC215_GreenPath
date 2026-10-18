"""Reload a StandardScaler plus independent Ridge heads from numeric arrays."""
import json
from pathlib import Path
import numpy as np
from greenpath_model.artifact import verify


class RegressionBundle:
    def __init__(self, directory):
        self.root = Path(directory)
        self.manifest = verify(self.root, "greenpath-ridge-v1")
        required = {"model.json", "weights.npz"}
        if not required.issubset(self.manifest["files"]):
            raise ValueError("Model metadata and weights must be checksummed")
        self.metadata = json.loads((self.root / "model.json").read_text())
        m = self.metadata
        self.dimensions = m["dimensions"]
        self.features = m["feature_columns"]
        for names in (self.dimensions, self.features):
            if not names or any(not isinstance(x, str) or not x for x in names) or len(names) != len(set(names)):
                raise ValueError("Feature and target names must be nonempty and unique")
        self.bounds = np.asarray(m["score_range"], dtype=float)
        if self.bounds.shape != (2,) or not np.isfinite(self.bounds).all() or self.bounds[0] >= self.bounds[1]:
            raise ValueError("Invalid score range")
        if m["model_version"] != self.manifest["artifact_version"] or m.get("transform_dtype") != "float32":
            raise ValueError("Model version or transform dtype mismatch")
        with np.load(self.root / "weights.npz", allow_pickle=False) as arrays:
            if set(arrays.files) != {"mean", "scale", "coef", "intercept"}:
                raise ValueError("Unexpected model array names")
            self.mean, self.scale, self.coef, self.intercept = (arrays[k].copy() for k in ("mean", "scale", "coef", "intercept"))
        n, d = len(self.features), len(self.dimensions)
        if self.mean.shape != (n,) or self.scale.shape != (n,) or self.coef.shape != (d, n) or self.intercept.shape != (d,):
            raise ValueError("Model array shape mismatch")
        if not all(np.isfinite(a).all() for a in (self.mean, self.scale, self.coef, self.intercept)) or (self.scale <= 0).any():
            raise ValueError("Invalid numeric model arrays")

    def predict(self, table):
        if table.empty or table.columns.duplicated().any() or set(table.columns) != {"cell_id", *self.features}:
            raise ValueError("Prediction table must contain cell_id and exactly the declared feature columns")
        ids = table.cell_id
        if not ids.map(lambda x: isinstance(x, str) and bool(x)).all() or ids.duplicated().any():
            raise ValueError("Prediction IDs must be nonempty and unique")
        x = table[self.features].to_numpy(dtype=np.float32, copy=True)
        if not np.isfinite(x).all():
            raise ValueError("Features must be finite")
        # Match StandardScaler's in-place float32 transform, including its rounding.
        np.subtract(x, self.mean, out=x)
        np.divide(x, self.scale, out=x)
        # Separate heads match the notebook's per-target Ridge prediction path.
        y = np.column_stack([x @ row + bias for row, bias in zip(self.coef, self.intercept)])
        if not np.isfinite(y).all():
            raise ValueError("Regression produced nonfinite values")
        return np.clip(y, *self.bounds)
