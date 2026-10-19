# Portable model loading and batch prediction

`RegressionBundle` validates a versioned model artifact and reloads its StandardScaler
state and independent Ridge heads from `weights.npz` with `allow_pickle=False`.
Prediction uses only NumPy; pandas/Parquet are used for batch input. A declared feature
order prevents swapped embedding columns from changing the model's meaning.

For local development, run from `src/model-deploy/`:

```sh
uv sync --frozen --python 3.11
uv run --frozen greenpath-predict --help
uv run --frozen pytest -q
```

The input is a Parquet table with a unique `cell_id` column and exactly the model's
feature columns. Output contains `scores.json` records compatible with the app's
record adapter, plus provenance and a checksum manifest. This describes
`greenpath-predict`; `greenpath-infer` additionally packages the geographic serving bundle
in the same job, matching explicit cell IDs rather than embedding row order.

New encoder-generated inputs must carry matching encoder provenance. Historical
notebook cache files are also accepted and explicitly marked in prediction metadata.
Their shape and columns alone cannot establish which encoder produced them.

This is batch inference, not a hosted endpoint. `greenpath-infer` also packages matching
H3 geometry and provenance for the API. `make run` selects the generated bundle; the
standalone app can still use its historical fixture. See [ML_PIPELINE.md](../../docs/ML_PIPELINE.md).
The Dockerfile builds a separate lightweight inference image. Build instructions are in
[ML_PIPELINE.md](../../docs/ML_PIPELINE.md).

Each new serving bundle records source workspace/snapshot, dataset, model, encoder and
run identity in checksummed provenance. Applications can retain several such bundles
and select one per request without loading training code or generating new predictions.
See [the version lifecycle](../../docs/DATA_AND_MODEL_VERSIONING.md).

## Pipeline inputs and outputs

Compose service `model-deploy` in `docker-compose.ml.yml` runs `greenpath-infer` after
`model-training`. It reads the current run's `model/`, `embeddings/embeddings.parquet`
and `prepared/` under `.artifacts/pipeline/runs/<run-id>/` and writes:

| Directory | Contents |
| --- | --- |
| `predictions/` | `scores.json`, `prediction.json`, `manifest.json` |
| `serving/` | `scores.json`, `region.geojson`, `schema.json`, `provenance.json`, `manifest.json` |

Prediction covers all prepared cells, including fitting and test cells; the map is not
an independent held-out evaluation. The container exits after packaging and has no
network access. The API reads the saved serving bundle; it does not invoke this model
container for each request.

From the repository root, `make run` runs the downstream pipeline and registers its
serving result. To display an existing local result without retraining, use
`make app-versions APP_ARGS="--version serving-YOUR-RUN-ID"`. To download a published
result first, run:

```sh
python3 tools/manage_artifacts.py fetch-serving --run-id YOUR-RUN-ID --version serving-YOUR-RUN-ID
```

The default remote path is
`gs://ac215-greenpath/artifacts/pipeline/runs/<run-id>/serving/`.
