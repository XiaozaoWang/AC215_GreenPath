# M1 training and export

This component extracts the selected M1 baseline from notebook 07. It fits a
StandardScaler on train plus validation, then one Ridge head per target using that
target's nonmissing labels. Alpha and encoder choice come from the historical
spatial-CV experiment, not a new search. Test rows are never used for fitting.

It exports numeric scaler/Ridge arrays rather than Python pickle objects. The model
component can reload them without scikit-learn, LightGBM or PyTorch. Before exposing
the artifact, training checks historical test predictions and reloads the exported
model in a fresh Python process.

From this directory:

```sh
uv sync --frozen --python 3.11
uv run --frozen greenpath-train --help
uv run --frozen pytest -q
```

See [the step-2 guide](../../docs/STEP2.md) for exact data paths, artifact commands,
GCS distribution and limitations. This directory does not yet include a Dockerfile;
container build and complete orchestration belong to the next pipeline stage.
