# One upstream container for notebooks 01-04

The `data-source` Compose service uses image `greenpath-data-source:ml` and runs all
four upstream stages sequentially in one container. The Python modules remain separate;
notebooks are not executed at runtime and the original notebooks are unchanged.

| Notebook | Python module | Individual CLI stage |
| --- | --- | --- |
| 01 streetview | `greenpath_source.streetview` | `streetview` |
| 02 aerial tiles | `greenpath_source.tiles` | `tiles` |
| 03 OSM / WorldCover | `greenpath_source.features` | `features` |
| 04 teacher labels | `greenpath_source.labeling` | `labeling` |

`greenpath_source.workflow` coordinates `all`. It verifies and reuses successful 01-03
outputs, runs missing stages, aggregates cached labels or performs an explicitly permitted
bounded label batch, and requires labeling completion before reporting success. One
workspace lock covers the entire combined execution. Progress is recorded in
`source-run-summary.json`; individual stage manifests and durable label caches remain.
A partial paid batch is saved but returns a failure until the selected workspace is complete.

The Dockerfile installs all four optional dependency groups from the existing shared
`uv.lock` with `uv sync --frozen --no-dev --all-extras` using uv 0.12.12 in `/opt/venv`.
No dependency versions changed for the container consolidation. For local development:

```sh
uv sync --frozen --all-extras
```

Build the shared image without acquiring data:

```sh
make source-build
```

Run 01-04 in one container, permitting at most 8 new labeled images (excluding retries):

```sh
make collect-sources SOURCE_ARGS="--allow-labeling --max-images 8"
```

The combined target uses the ADC override. Without paid permission, it requires a complete
label cache before acquisition and makes zero new labeling calls. For the full source-to-app
workflow, `make run-all` invokes this same `all` command, then freezes the snapshot and
starts the downstream pipeline. It mounts ADC only when new labeling is permitted.

The existing `make collect-streetview`, `make collect-tiles`, `make collect-features`,
`make label-images SOURCE_MAX_IMAGES=8` and `make label-report` remain available. Each
standalone invocation creates its own temporary container from the same shared image;
it does not create four stage-specific images. Explicitly invoking 01 after 02 still
refuses to reset downstream columns; use `all` for verified stage reuse.

The ADC mount is read-only. In the combined paid job it is available throughout the
container, rather than isolated to a separate labeling container. Credentials are never
copied into the image or written to source records.

The consolidated image completed the `boston-pilot-v2` source-to-app pilot on 2026-10-19.
See the main pipeline guide for recorded evidence and scope.
Optional coordination checks (no actual source stages): `make source-test`.
See [ML_PIPELINE.md](../../docs/ML_PIPELINE.md) and
[DEPENDENCIES.md](../../docs/DEPENDENCIES.md).

## Research notebook archive

Original and English research notebooks are retained unchanged in
[docs/notebooks/](../../docs/notebooks/README.md). Container execution uses the extracted Python
modules, not the notebook files. Extraction comments retain their original notebook
basenames; `notebook-provenance.json` records their current repository paths.
