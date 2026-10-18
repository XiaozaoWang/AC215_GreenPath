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
