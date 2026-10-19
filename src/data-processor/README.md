# Data ingestion and preparation

This is the first of four downstream ML jobs. It reads a frozen snapshot of notebook
01-04 outputs from a local directory or GCS, safely extracts NAIP images, then executes notebook 05's
join/filter/spatial-split/buffer/fold logic. It does not reacquire Mapillary imagery,
query OSM or invoke a paid teacher. Those upstream operations run in the separate `data-source` container; this component
starts from their frozen outputs.

For local development, run from `src/data-processor/`:

```sh
uv sync --frozen --python 3.11
uv run --frozen greenpath-data --help
uv run --frozen pytest -q
```

`snapshot` freezes current processed tables and valid aerial images into a new version.
`prepare` validates a local snapshot and builds the dataset. `ingest` downloads/verifies
source and encoder artifacts with read-only ADC, then prepares the dataset. `--offline`
requires pre-existing verified caches and never contacts GCS.

Smoke mode selects 32 train, 8 validation and 8 test cells *after* full spatial splitting
and buffering. It is a 48-image execution check, not the historical 188-cell evaluation.
Full mode keeps all qualifying cells. Both modes record hashes, split counts, filtering
parameters, image provenance and teacher-label limitations. Existing artifact directories are never overwritten; choose a new run ID for another run.

All images are checked as RGB 256x256 tiles. CLIP crop/normalization is executed by the
next component. Unsafe archives, broken hashes and ambiguous IDs fail before publication.
Build the image from the repository root following [ML_PIPELINE.md](../../docs/ML_PIPELINE.md),
so the shared sibling model package is included.

## New source snapshots and archives

A new source workspace can be frozen after its selected-image labeling is complete.
The snapshot preserves aggregate/image labels, source configuration and run records.
The standalone `archive` utility can create a `workspace.tar` backup of raw images,
OSM PBF and label journals; `--snapshot-dir` binds it to the frozen source files under
an exclusive source lock. Current `make publish-run ... PUBLISH_ARGS="--include-upstream"`
publishes the source workspace as individual files instead, not as this legacy tar bundle.

The training launcher now accepts `--source-dir` to prepare a local immutable snapshot
without requiring an intermediate GCS upload. Encoder weights still use their verified
cache/GCS source. Dataset identity derives from the source manifest, contract, preparation
recipe and mode rather than a universal `pipeline-full-v1` label. See
[DATA_AND_MODEL_VERSIONING.md](../../docs/DATA_AND_MODEL_VERSIONING.md) for the version chain.

## Pipeline inputs and outputs

- Frozen input: `.artifacts/snapshots/sources/<snapshot-version>/`, or a verified GCS
  download in `.artifacts/pipeline/cache/sources/<snapshot-version>/`.
- Shared caches: extracted tiles in `.artifacts/pipeline/cache/images/<snapshot-version>/`
  and fixed CLIP weights in `.artifacts/pipeline/cache/encoders/<encoder-version>/`.
- Output: `.artifacts/pipeline/runs/<run-id>/prepared/`, including `datasets/<dataset-id>/`
  (dataset, schema and split Parquet files), `images.json`, `region.geojson`, `schema.json`,
  `training-config.json`, `preparation.json` and `manifest.json`.

From the repository root, `make run PIPELINE_ARGS="--run-id YOUR-RUN-ID"` runs this job
and the remaining downstream jobs, then starts the app. Add `--source-dir` to select a
local frozen snapshot. The default GCS snapshot prefix is
`gs://ac215-greenpath/artifacts/snapshots/sources/`; ingestion requires ADC unless the
necessary inputs are already cached and `--offline` is selected.
