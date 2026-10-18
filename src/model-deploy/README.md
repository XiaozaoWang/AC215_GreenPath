# Portable model loading and batch prediction

`RegressionBundle` validates a versioned model artifact and reloads its StandardScaler
state and independent Ridge heads from `weights.npz` with `allow_pickle=False`.
Prediction uses only NumPy; pandas/Parquet are used for batch input. A declared feature
order prevents swapped embedding columns from changing the model's meaning.

```sh
uv sync --frozen --python 3.11
uv run --frozen greenpath-predict --help
uv run --frozen pytest -q
```

The input is a Parquet table with a unique `cell_id` column and exactly the model's
feature columns. Output contains `scores.json` records compatible with the app's
record adapter, plus provenance and a checksum manifest. Geography is not inferred
from embedding row order; geographic app integration remains a separate step.

New encoder-generated inputs must carry matching encoder provenance. Historical
notebook cache files are also accepted and explicitly marked in prediction metadata.
Their shape and columns alone cannot establish which encoder produced them.

This is batch inference, not a hosted endpoint. The existing API still uses its
step-1 geographic fixture. See [STEP2.md](../../docs/STEP2.md).
The Dockerfile builds a separate lightweight inference image. Build instructions are in
[CONTAINERS.md](../../docs/CONTAINERS.md).
