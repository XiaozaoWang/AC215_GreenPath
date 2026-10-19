# Data and Model Versioning

## Start with three places

The folders answer three different questions:

| Place | Think of it as | Question it answers |
| --- | --- | --- |
| `sources/` | Materials still being assembled | What have we collected and labeled so far? |
| `snapshots/sources/` | A fixed input package | Exactly which inputs were selected for modeling? |
| `pipeline/runs/<run-id>/` | A complete experiment folder | What data, model and results did this execution produce? |

All three live under `.artifacts/`. **Shared inputs are stored by version; downstream
outputs are grouped by run, then by stage.** It is not an either/or choice between
stage-based storage and run-based storage.

```text
01-04 -> source workspace -> freeze -> snapshot
                                         |
                                         v
                              05 -> 06 -> 07 -> prediction -> serving
                              └── pipeline/runs/<run-id>/ ──────────┘
```

The source workspace and snapshot remain outside the run directory. The run records
which snapshot it used; it does not need to repeat acquisition. Choosing an existing
snapshot is a supported starting point, not a different storage design.

## 1. Source workspace: finish collecting and labeling

Example: `.artifacts/sources/boston-pilot-v2/`.

This is the working area for 01-04. Files can be added and aggregate tables updated
as work progresses. Labels live here before a training snapshot exists.

| Path | Purpose |
| --- | --- |
| `raw/mapillary/images/` and `metadata/` | Street photos and request caches |
| `raw/grid/`, `raw/osm/` | H3 grid and OSM source files |
| `processed/tiles/` | Aerial PNGs and source sidecars |
| `processed/cells.parquet`, `streetview.parquet`, `osm_features.parquet` | Aligned cell, street-image and geographic tables |
| `processed/label-cache/` | Durable per-image teacher results; keep for paid-call reuse |
| `processed/labels_image.parquet`, `labels_cell.parquet` | Image labels and aggregated cell targets |
| `processed/labels_meta.json` | Label configuration, usage and completion status |
| `source-workspace.json`, `runs/`, `source-run-summary.json` | Configuration binding, stage manifests and combined-job progress |

This workspace can change as collection/labeling resumes. Its source version is bound
to the configuration hash. Changed collection or labeling configuration requires a new
source version and directory; changing an operational batch-size limit does not.

## 2. Snapshot: one fixed input, reusable across runs

Example: `.artifacts/snapshots/sources/boston-pilot-v2-frozen-v1/`.

A changing directory is a poor record of training inputs: the same path may contain
more labels tomorrow. Freezing captures selected inputs so later workspace updates
do not change what a saved experiment means. A snapshot stores inputs, not a trained model.

Freezing requires completion of the selected workspace's labeling. It saves:

- `processed/`: selected input tables and label metadata; image-level labels when available.
- `tiles.tar`: selected aerial images, not the entire raw street-image collection.
- `selected-config.json`: selected training configuration.
- `source.json`: origin and input file hashes.
- `manifest.json`: artifact version, file sizes and SHA-256 checksums.
- `upstream/`: source workspace identity and stage records for new-pipeline snapshots.

An existing snapshot version cannot be overwritten with different contents. Freezing
separates fixed training inputs from the workspace's changing progress. A complete raw
workspace backup is optional and separate (`source-archives/`).

**Historical input is an exception in location, not in meaning:** default `make run`
uses `gs://ac215-greenpath/pipeline-inputs/boston-processed-v1`, downloaded to
`.artifacts/pipeline/cache/sources/boston-processed-v1/`. It does not automatically select
the newest local snapshot. Use `--source-dir` to choose a local frozen input explicitly.

## 3. Run: keep matching outputs together

The snapshot has labels and images, but not yet the final modeling rows/splits. Stage 05
creates those; 06 turns images into numeric features; 07 fits model parameters. Prediction
then uses the saved parameters to produce scores and a serving package.

Keeping these stage outputs together makes the chain explicit: **this prepared dataset
-> these features -> this model -> these predictions -> this application result**.


```text
.artifacts/pipeline/runs/<run-id>/
├── prepared/
│   ├── datasets/<dataset-version>/    dataset/train/val/test.parquet, schema.json
│   ├── images.json                   Cell-to-image input list
│   ├── region.geojson, schema.json    Geometry and output contract
│   └── preparation.json              Input identity, filters and split counts
├── embeddings/                       embeddings.parquet + embedding.json
├── model/                            weights.npz + model.json
├── predictions/                      scores.json + prediction.json
├── serving/                          scores.json + region.geojson + schema.json
│                                     + provenance.json
├── logs/                             Build, ML and app startup logs
├── run-summary.json                  Success/failure and verification
└── sample-request.json, sample-response.json
```

Each of the five stage artifact directories also has `manifest.json`. `prepared/` includes
`training-config.json`; `model/` includes `metrics.json`, `split-ids.json` and `validation.json`.
Together these identify what was fitted and whether model reload reproduced predictions.

The dataset is the snapshot after joins, filtering and spatial splitting. New code uses
a content-derived `dataset-<mode>-<hash>` ID; older IDs such as `pipeline-smoke-v1` remain
valid. Identical preparation inputs/rules can have the same dataset ID in different runs,
even though each run saves its own prepared copy.

| Identity | Meaning |
| --- | --- |
| Source version | A collection/labeling workspace |
| Snapshot version | A fixed input package |
| Dataset version | Prepared modeling rows and splits |
| Run ID | One downstream execution |
| `model-<run-id>` | Its saved model artifact |
| `serving-<run-id>` | Its application result artifact |

Each container's versions are recorded where it reads/writes: source stage manifests;
`preparation.json`; `embedding.json`; `model.json`; `prediction.json`; serving manifest and
`provenance.json`. Run summaries connect these records. Hashes check integrity, not authorship.

## Model parameters versus data

A feature table, a trained model and predicted scores are three different things:
features describe the images; model parameters define the learned mapping; scores are
what that mapping has already calculated. Saving one does not replace the other two.
The table uses paths relative to `.artifacts/`.


| File | What it contains |
| --- | --- |
| `pipeline/cache/encoders/clip-b16-laion2b-v1/model.safetensors` | Fixed pretrained CLIP weights |
| Same encoder directory: `encoder.json` | Weight identity and image preprocessing/TTA settings |
| `pipeline/runs/<id>/model/weights.npz` | Learned scaler `mean`, `scale`; Ridge `coef`, `intercept` |
| `pipeline/runs/<id>/model/model.json` | Feature/target order, encoder reference and training settings |
| `pipeline/runs/<id>/embeddings/embeddings.parquet` | Computed features, not model weights |
| `pipeline/runs/<id>/serving/scores.json` | Computed predictions, not teacher labels or model weights |

A serving package puts scores alongside their grid boundaries, target schema and provenance
so the API can display them without loading the image encoder or regression model.
Opening the map reads saved results; entering preferences computes a match against those
results. It does not train or re-predict the images.

## Select, publish and share

Two runs can share a snapshot while keeping separate models/serving. A new run refuses
an existing run directory. Catalog registration refuses a serving ID with changed contents.

```sh
make catalog-refresh
python3 tools/manage_artifacts.py list
make app-versions APP_ARGS="--version boston-heldout-en-v2"
# After the corresponding run succeeds:
make app-versions APP_ARGS="--version serving-boston-pilot-v2"
```

`.artifacts/catalog/serving.json` stores paths and pinned manifest hashes, not score copies.
A successful pipeline selects its new serving as default; this does not overwrite older
results. The frontend dropdown changes the displayed version without retraining.

New outputs stay local until explicit publication:

```sh
# Review a successful run first. Use the actual training ID after workflow retries.
make publish-run RUN_ID=boston-pilot-v2 PUBLISH_ARGS="--include-upstream"
```

GCS prefixes under `gs://ac215-greenpath/` are `pipeline-inputs/`, `datasets/`, `features/`,
`models/`, `predictions/`, `serving/` and `releases/`, each with its artifact version.
`--include-upstream` adds `source-archives/`; it requires new-pipeline lineage and unchanged
source inputs. Historical runs can publish without this option. Release links are published
last; local release records are under `.artifacts/releases/` and `catalog/publications/`.

```sh
# Replace other-run with a real published ID, then refresh the frontend version list.
python3 tools/manage_artifacts.py fetch-serving \
  --uri gs://ac215-greenpath/serving/serving-other-run \
  --version serving-other-run
```

Local `.artifacts/` is where jobs read and write during execution. GCS is where explicitly
published versions can be shared with teammates or recovered on another machine. Publishing
copies selected artifacts to cloud storage; it does not move them out of the local folders,
automatically synchronize future changes, or deploy the application.
`pipeline/cache/` stores reusable downloads; `workflows/<id>/` stores full-workflow records
and the actual `training_run_id`.

## Archived legacy artifacts

Earlier standalone inputs are now under `.artifacts/archived/`: `encoders/`,
`models/m1-clip-ridge-v1/`, `samples/`, `training-inputs/` and `boston-heldout-en-v2/`.
Legacy Make commands retain their original paths and need the corresponding directory
restored before use. `.artifacts/archived/README.md` and `ARCHIVE_INDEX.json` record the
exact original locations. Restore only when the destination is absent; do not overwrite
newly created files. The current three main entry points use pipeline caches/runs and the
API's built-in historical fixture, which were not moved.

Old downloaded/preflight copies and explicitly selected test outputs were deleted.
Real source workspaces, paid label caches, snapshots and registered successful runs remain.
