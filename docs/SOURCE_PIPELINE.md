# Notebook 01-04 in one upstream container

For the one-command source-to-app workflow, use [END_TO_END.md](END_TO_END.md).
The individual commands here remain available. Freezing a new source workspace now
requires its selected-image labeling to be complete; partial batches are not full datasets.

## Scope and status

These commands extract the upstream notebook logic without redesigning the application
or the existing four-job training workflow. Each stage remains an independent Python command
inside the shared `greenpath-data-source:ml` image. The `all` command executes the four
stages sequentially in one `data-source` container with verified stage reuse. Runtime does not load `.ipynb` files.

**Verification status:** the user ran all four stages before the uv dependency migration:
50 cells, 192 street images, 50 usable aerial tiles, 50 geographic feature rows and 9
labeled images. Labeling remains partial. Those output checks predate the new locked
image builds; rebuilding and runtime verification of the migration remain user tasks.
No acquisition, VLM calls or tests were run during the dependency change.

All Python images now follow the same `pyproject.toml` + `uv.lock` convention. The upstream
image installs all stage extras with `uv sync --frozen --no-dev --all-extras`. See
[DEPENDENCIES.md](DEPENDENCIES.md) for shared rules and the component matrix.

`make run` continues to use the published historical input snapshot. It does not start
any of these jobs. New source outputs are not automatically published or adopted by the app.

## Prepare a dedicated workspace

Run from the repository root. The default configuration selects up to 50 eligible cells
for acquisition, matching the notebook method but limiting initial downloads. This is
an acquisition trial, not a guarantee of enough spatially separated rows for the downstream
48-cell smoke split. Use a new configuration/version for a larger collection.

- Config: `configs/source-boston.example.json`
- Local data: `.artifacts/sources/boston-source-local-v1/`
- Dataset identity: `boston-source-local-v1`

Use the defaults initially, or copy the configuration to a new JSON file and set a new
`source_version`. Set `SOURCE_CONFIG` and `SOURCE_DATA_DIR` to the corresponding file and
an empty directory. Do not point these jobs at the historical notebook data directory.

```sh
export SOURCE_CONFIG="$PWD/configs/source-boston.example.json"
export SOURCE_DATA_DIR="$PWD/.artifacts/sources/boston-source-local-v1"
mkdir -p "$SOURCE_DATA_DIR"
```

A marker binds the workspace to the full configuration hash. Changing configuration
requires a new version and workspace; operational `--max-images` limits can change
without invalidating it. An OS file lock prevents concurrent stages in the same directory.
Do not delete the marker or lock files to bypass identity checks.

Mapillary reads `MAPILLARY_TOKEN` from the container environment. Set it in your local
shell or an ignored `.env`, never in JSON or Git. If entering it interactively in zsh:

```sh
read -s 'MAPILLARY_TOKEN?Mapillary token: '
export MAPILLARY_TOKEN
```

Vertex labeling requires authorized ADC, a billing-enabled project, Vertex AI access,
and a model available to that project/location. The model name is inherited from the
notebook configuration; availability has not been verified. Set:

```sh
export GOOGLE_CLOUD_PROJECT=organic-victory-508017-v9
export GOOGLE_CLOUD_LOCATION=us-central1
export ADC_FILE="$HOME/.config/gcloud/application_default_credentials.json"
```

The optional GCP override mounts ADC read-only into the shared service. During a combined
paid job, all stages share that container and can see the credential mount. Standalone
acquisition commands omit the override. No job copies credentials into an image.

## Run 01-04 in one container

```sh
make source-build
make collect-sources SOURCE_ARGS="--allow-labeling --max-images 8"
```

The second command permits a bounded paid batch. If labels remain pending it exits with
an error after persisting results; repeat explicitly to continue. It is not an automatic
loop. The combined command holds a workspace lock, verifies prior stage manifests and
skips valid completed 01-03 outputs. It saves `source-run-summary.json` in the workspace.
The existing full `make run-all` command now invokes one such upstream container rather
than creating a separate container for each stage. Its limit comes from the workflow JSON.
With a matching frozen snapshot, run-all still skips upstream work entirely.
The consolidated image and execution path await user runtime verification.

## Run stages individually

Each target uses the same shared image and runs one selected stage in a new temporary container. Start with these commands
in order; do not run stage commands concurrently against the same directory.

### 01: street-view collection

```sh
make collect-streetview
```

Fetch zoom-14 vector tiles, decode/filter metadata, assign H3 resolution-10 cells,
select heading-diverse photos, compute coverage, sample cells and download street images.
Existing metadata tiles and valid downloaded images are reused. Download writes are atomic.
Only successfully downloaded, decodable images contribute to `n_images`.

Outputs include `processed/streetview.parquet`, `processed/cells.parquet`, raw metadata,
raw images, the full grid, coverage and street-view preview PNGs, and a run manifest.
Rerunning 01 after 02 has added tile columns is refused because it would reset the cell
master table. To recollect, start a new version/workspace; to resume later work, run that
later stage directly.

### 02: NAIP aerial tiles

```sh
make collect-tiles
```

Query Planetary Computer, prefer recent imagery covering the full footprint, read remote
COGs, crop/resample RGB tiles and update the cell master table. Sidecar records bind cached
PNGs to the NAIP item, coordinates, ground extent, pixel size and image hash, retaining
nodata and ground-resolution metadata. Failed crops are retried on a later invocation.

Outputs: `processed/tiles/*.png` and sidecars, updated `processed/cells.parquet`, tile
preview PNG, year-gap counts and a run manifest. Check `tile_ok`, `error` and `nodata_frac`.
Individual failed crops remain visible; if no tile is usable the command fails.

### 03: OSM and WorldCover features

```sh
make collect-features
```

Download/reuse the Geofabrik PBF, retain its source metadata and SHA-256, read roads/areas/
POIs with pyrosm, project to local UTM and compute statistics on the tile footprint.
Read WorldCover classes from Planetary Computer. Produce one row per H3 identity.

The WorldCover dominant-class expression is corrected to use the most frequent class
code. Overlapping source tiles are handled once per cell, and incomplete coverage fails
explicitly rather than silently producing an incomplete feature table.

Outputs: `processed/osm_features.parquet`, feature preview PNG and a run manifest. The
PBF is cached locally; expensive feature computation is rerun if this stage is invoked
again. A Geofabrik `latest` URL is mutable, so retain the downloaded file and manifest
for exact input reuse. New workspaces are not expected to reproduce historical values.

### 04: paid teacher labeling

```sh
make label-images SOURCE_MAX_IMAGES=8
```

This explicitly calls Vertex AI for at most 8 **new images**, excluding retries. Results
are normalized and aggregated exactly as in the notebook: usable scores in 1-5 become
`(score - 1) / 4`; too few usable photos yield missing cell labels, not zero labels.

The first run is intentionally partial. Check the gallery, then repeat with the desired
finite image limit to continue. Already cached current images are skipped. Positive
limits do not guarantee exactly that many successes; failures and unusable images are recorded.

The cache identity includes image ID/content, full prompt hash, model, location, thinking
configuration and response-schema identity. Each successful paid result is atomically
journaled before batch aggregation. A crash after the remote service has answered but
before the local write can still require a repeated request; exactly-once billing is not
promised. Auth/model errors stop queued work, but requests already in flight may finish.

`SOURCE_MAX_IMAGES` is an image limit, not a dollar budget or total HTTP attempt limit.
SDK/application retries can add calls. Token usage is recorded. The old notebook price
constants are not presented as current prices: cost estimates are null unless you supply
rates in a new configuration after checking your actual billing terms.

Outputs:

```text
processed/
├── label-cache/*.json          # durable per-image results; retain for resuming
├── labels_image.parquet        # rebuilt cache table
├── labels_cell.parquet         # downstream-compatible 8-dimensional labels
├── labels_meta.json            # prompt/model/tokens/completeness
├── label_errors.json
├── labels_gallery.html
├── label_distributions.png
├── labels_quality.parquet
├── labels_correlations.parquet # when geographic features exist
└── qa/
    ├── human_images/
    └── human_label_sheet.csv
```

`labels_meta.json` explicitly records `complete` and `images_pending`. A successful
limited batch is not a claim that the entire dataset was labeled. Existing human sheets
are preserved on resume; they may represent an earlier subset. Copy/rename reviewed
sheets before deliberately creating a new sampling sheet. No human agreement is computed
without actual ratings. The sandbox's automatic uncached demonstration calls are omitted;
use a small cached labeling batch plus the HTML gallery to inspect the same scores safely.

To regenerate aggregate labels and reports from existing cache **without model calls**:

```sh
make label-report
```

No ADC override is needed for that command. It fails clearly if no current labels exist.

## Handoff to the existing training pipeline

The outputs retain the notebook-relative paths and columns consumed by the existing
`greenpath-data snapshot` command. The source workspace is local staging; GCS remains the
shared store for frozen snapshots. Nothing is uploaded automatically.

Before freezing, inspect the reports and label completeness. A partial label run can leave
many cells missing and therefore unsuitable for the current downstream smoke/full split.
The new workspace is not expected to have historical 651/173/188 counts.

After choosing a sufficiently large, reviewed dataset, reuse the existing snapshot export.
The selection config below comes from the historical source cache populated by `make run`;
it reuses prior CLIP/Ridge selection, not a new hyperparameter search.

```sh
mkdir -p .artifacts/source-snapshots
# Build only if this existing data image is absent or its code changed.
docker build -t greenpath-data-processor:ml -f src/data-processor/Dockerfile .
docker run --rm --user "$(id -u):$(id -g)" \
  --mount type=bind,source="$SOURCE_DATA_DIR",target=/source,readonly \
  --mount type=bind,source="$PWD/.artifacts/source-snapshots",target=/exports \
  --mount type=bind,source="$PWD/.artifacts/pipeline/cache/sources/boston-processed-v1/selected-config.json",target=/selection.json,readonly \
  greenpath-data-processor:ml snapshot \
  --data-root /source --config /selection.json \
  --output /exports/boston-source-local-v1 --artifact-version boston-source-local-v1
```

The snapshot also carries the optional `source-workspace.json` and stage run manifests
under `upstream/`, preserving collection configuration and lineage. Historical sources
without those files retain their previous export behavior.

The existing snapshot packs aerial PNGs and processed source tables, not street-view
photos. Preserve raw street photos, PBF and label-cache journals locally for now; review
source attribution/access terms before sharing raw imagery. A frozen training snapshot
alone is not the full upstream archive.

Publish to a **new** GCS prefix using the existing artifact publisher, only when ready:

```sh
cd api-service
uv run --frozen --python 3.12 python -m api.artifacts publish \
  gs://ac215-greenpath/pipeline-inputs/boston-source-local-v1 \
  ../.artifacts/source-snapshots/boston-source-local-v1
cd ..
```

Select that snapshot explicitly, keeping the current default intact:

```sh
PIPELINE_SOURCE_URI=gs://ac215-greenpath/pipeline-inputs/boston-source-local-v1 \
PIPELINE_SOURCE_VERSION=boston-source-local-v1 \
make run PIPELINE_ARGS="--run-id new-source-smoke-001"
```

The source version must match its manifest. `docker-compose.ml.yml` now parameterizes
that source identity and the corresponding image-cache path; its previous defaults are
unchanged. No new encoder, training algorithm, database, orchestration platform or
application UI is introduced by this extraction.

## Suggested user verification

1. Build the images and resolve any platform/dependency errors first.
2. Run 01-03 on the small configuration and inspect tables/images/reports.
3. Run a limited paid 04 batch, then inspect its journal and gallery.
4. Run `make label-report` to confirm cache-only reporting without API access.
5. Repeat the paid command to confirm existing results are skipped and new ones saved.
6. Expand collection only under a new source version/workspace when necessary.
7. Freeze/publish only a reviewed dataset, then test the existing training pipeline with
   an explicit source URI/version and a new run ID.

Run manifests capture configuration, input/output hashes and extraction source hashes;
they are provenance records, not signatures or proof of model/label quality. Validation of the uv-built images is pending the user's execution.
