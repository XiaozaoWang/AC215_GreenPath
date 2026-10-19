# M1 training and export

This component extracts the selected M1 baseline from notebook 07. It fits a
StandardScaler on train plus validation, then one Ridge head per target using that
target's nonmissing labels. Alpha and encoder choice come from the historical
spatial-CV experiment, not a new search. Test rows are never used for fitting.

It exports numeric scaler/Ridge arrays rather than Python pickle objects. The model
component can reload them without scikit-learn, LightGBM or PyTorch. Before finalizing
the artifact, training verifies exported-model predictions against the fitted model,
including a reload in a fresh Python process. Comparing against historical test
predictions is an additional check only when `--reference-policy required` is selected.

For local development, run from `src/model-training/`:

```sh
uv sync --frozen --python 3.11
uv run --frozen greenpath-train --help
uv run --frozen pytest -q
```

See [the ML pipeline guide](../../docs/ML_PIPELINE.md) for exact data paths, artifact commands,
GCS distribution and limitations. Build the Dockerfile from the repository
root as explained in [ML_PIPELINE.md](../../docs/ML_PIPELINE.md).

New pipeline datasets use a verified `--embeddings` artifact and `--reference-policy none`.
The default remains strict historical reproduction. Neither mode fits on test rows.

## Pipeline inputs and outputs

Compose service `model-training` in `docker-compose.ml.yml` runs after `feature-encoder`:

- Prepared data, split IDs, schema and training config: `.artifacts/pipeline/runs/<run-id>/prepared/`.
- Fresh embeddings: `.artifacts/pipeline/runs/<run-id>/embeddings/embeddings.parquet`.
- Encoder identity/weights: `.artifacts/pipeline/cache/encoders/<encoder-version>/`.
- Output: `.artifacts/pipeline/runs/<run-id>/model/`.

`weights.npz` stores scaler means/scales and Ridge coefficients/intercepts. `model.json`
records dimensions, feature order, encoder identity and hyperparameters; `metrics.json`
reports held-out test results, `split-ids.json` records fitting/test IDs, and
`validation.json` records reload checks. `manifest.json` covers these files with checksums.
The current pipeline uses Ridge alpha 1000 from the prior experiment; it does not rerun
all notebook 07 experiments or tune on the test set.

From the repository root, `make run` includes training automatically. This container
has no network access, writes locally and exits. GCS publication uses the separate
`make publish-run RUN_ID=YOUR-RUN-ID` command; uploaded model files retain the path
`artifacts/pipeline/runs/<run-id>/model/`.
