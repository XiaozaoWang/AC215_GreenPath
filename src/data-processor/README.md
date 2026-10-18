# Data ingestion and preparation

This is the first of four ML jobs. It ingests a versioned GCS snapshot of existing
notebook 01-04 outputs, safely extracts NAIP images, then executes notebook 05's
join/filter/spatial-split/buffer/fold logic. It does not reacquire Mapillary imagery,
query OSM or invoke a paid teacher. Those upstream operations remain research notebooks;
their cached outputs are the documented starting boundary of this reproducible pipeline.

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
parameters, image provenance and teacher-label limitations. Original v1 is never overwritten.

All images are checked as RGB 256x256 tiles. CLIP crop/normalization is executed by the
next component. Unsafe archives, broken hashes and ambiguous IDs fail before publication.
Build the image from the repository root following [CONTAINERS.md](../../docs/CONTAINERS.md),
so the shared sibling model package is included.

## New source snapshots and archives

A new source workspace can be frozen after its selected-image labeling is complete.
The snapshot preserves aggregate/image labels, source configuration and run records.
`archive` separately backs up raw images, OSM PBF and paid label journals; `--snapshot-dir`
binds that archive to the frozen source files under an exclusive source lock.

The training launcher now accepts `--source-dir` to prepare a local immutable snapshot
without requiring an intermediate GCS upload. Encoder weights still use their verified
cache/GCS source. Dataset identity derives from the source manifest, contract, preparation
recipe and mode rather than a universal `pipeline-full-v1` label. See
[DATA_LIFECYCLE.md](../../docs/DATA_LIFECYCLE.md) for the version chain.
