> **Milestone 2 integration branch.** `make run` trains from a frozen source snapshot. `make run-all` adds explicit upstream acquisition and bounded teacher labeling before training. `make app-versions` opens historical and successful local results without training. See [the complete workflow guide](docs/END_TO_END.md) and [data/version rules](docs/DATA_LIFECYCLE.md). New orchestration and version selection await user runtime verification; earlier successful runs are retained.

> Earlier work: [step 1](docs/STEP1.md) established the geographic model contract; [step 2](docs/STEP2.md) exported the original M1 model. [Container builds](docs/CONTAINERS.md) and [app wireframe](docs/APP_DESIGN.md) document the current integration.

# GreenPath: data workflow and application

GreenPath uses a teacher/student approach. A vision-language teacher, such as Gemini, scores **street-view images** to create perception labels Y. A student model learns to predict those labels from **aerial images and OSM features**, the inputs X. Street-view images are used to create training labels; they are not needed for student inference.

The research notebooks turn raw inputs into a training table with one row per H3 cell. Notebooks 01 through 07 cover collection, labeling, dataset construction, feature extraction and baseline evaluation.

```text
01 Street view -> cells.parquet -> 02 Aerial imagery -> 03 OSM / WorldCover features
                    |
                    +-> 04 VLM labeling -> 05 Dataset and spatial splits
                                                |
                                                +-> 06 Image embeddings
                                                +-> 07 Baselines and linear probes
```

Collection starts with street-view coverage. Those locations determine which H3 cells receive aerial imagery and geographic features, avoiding unnecessary downloads for cells without labels.

## Select saved results or collect a new pilot

```sh
# No training or teacher calls: inspect registered results in the version selector.
make app-versions

# Explicit paid-labeling permission; finishes the selected pilot and runs through the app.
make run-all WORKFLOW_ARGS="--run-id pilot-e2e-001 --allow-labeling"
```

The default pilot config reuses the existing 50-cell workspace where outputs match,
then uses its own new frozen source snapshot. It never falls back to historical inputs
if the new pilot is incomplete. See [END_TO_END.md](docs/END_TO_END.md) for limits,
resuming, snapshot selection and explicit GCS publication.

## Run the container pipeline

With Docker, Python 3.9+, make and authorized GCS ADC ready:

```sh
make run
```

Frontend: http://localhost:3030; API docs: http://localhost:9030/docs.
The four ML jobs run in order and exit; the two app containers continue running.
See [PIPELINE.md](docs/PIPELINE.md) for prerequisites, versions, offline/full options,
logs and exact scope. Project code, comments and new submission documentation are English.

## Run only the standalone application

With Docker Desktop running, from the repository root:

```sh
docker compose up --build
```

- Frontend: http://localhost:3000
- API documentation: http://localhost:9000/docs
- Default preference parser: offline English keyword rules (`LLM_PROVIDER=mock`).
- Default vision scores: real saved M1 predictions for 188 held-out Boston cells.
- Current English-only fixture: `boston-heldout-en-v2`.

This standalone command uses the bundled historical prediction fixture. `make run` instead generates and selects a fresh model-derived serving bundle. GCS setup and the historical artifact commands are documented in [docs/STEP1.md](docs/STEP1.md). Credentials are not required for the standalone bundled example.

## Research notebook setup

1. Use the research Python 3.11 environment and select its kernel in VS Code. Notebook dependency cells document required packages. The API has a separate Python 3.12 environment.
2. Copy `.env.example` to `.env`. Notebook 01 needs a Mapillary access token, not an app ID or client secret. Never commit `.env`.
3. Run notebooks in order: later notebooks consume earlier outputs.
4. Data defaults to `./data/`. `GREENPATH_ROOT` and each notebook's storage configuration control alternative locations. Research storage settings are separate from the app artifact CLI.

The notebooks support resuming by skipping outputs that already exist.

## Notebook responsibilities

Research notebooks are archived in [docs/notebooks/](docs/notebooks/README.md).
Both original and `en_` versions are retained without content changes. The container
pipeline runs Python modules from `src/` and does not execute these notebooks.

| Notebook | Purpose | Main outputs |
| --- | --- | --- |
| [01_streetview.ipynb](docs/notebooks/01_streetview.ipynb) | Collect Mapillary metadata, filter images, assign H3 cells and download samples | `processed/cells.parquet`, `processed/streetview.parquet`, raw images and grid |
| [02_tiles.ipynb](docs/notebooks/02_tiles.ipynb) | Extract a 256 x 256 NAIP aerial tile for each selected cell | `processed/tiles/*.png`, imagery metadata in the cell table |
| [03_osm_features.ipynb](docs/notebooks/03_osm_features.ipynb) | Compute OSM and WorldCover features | `processed/osm_features.parquet` |
| [04_labeling.ipynb](docs/notebooks/04_labeling.ipynb) | Score street-view images using Gemini on Vertex AI and aggregate labels | `labels_image.parquet`, `labels_cell.parquet`, labeling metadata and QA exports |
| [05_build_dataset.ipynb](docs/notebooks/05_build_dataset.ipynb) | Join inputs and labels, filter rows, create spatial splits and freeze a version | `datasets/<version>/` tables, schema, manifest and dataset card |
| [06_embeddings.ipynb](docs/notebooks/06_embeddings.ipynb) | Extract frozen ResNet-50, DINOv2-S and CLIP ViT-B/16 features | `embeddings/v1/*.parquet`, metadata |
| [07_baselines.ipynb](docs/notebooks/07_baselines.ipynb) | Select and evaluate B0, B1, M1 and M3 baselines | `results/<run_id>/`, experiment log |

### 01: street-view collection

The pilot bounding box is divided into zoom-14 Mapillary vector tiles. Metadata includes locations, capture dates, headings, panorama flags and quality information. Vector tiles avoid unstable dense-area results from bounding-box search.

Filtering keeps images from 2019 onward, excludes December through March and panoramas, and requires headings. Coordinates map to H3 resolution 10. Each cell keeps up to four images with distributed headings, choosing the latest image within each heading quadrant. Coverage is reported before sampling eligible cells and downloading 1024-pixel thumbnails.

The documented Boston/Cambridge pilot contains 3,532 grid cells. About 1.66 million metadata points were reduced to approximately 100,000 usable images; 1,127 cells (31.9%) met the coverage rule. Winter exclusion was the largest source of reduction. These are collection-stage figures, not the final frozen dataset counts.

### 02: aerial imagery

For each selected cell, the notebook finds a recent NAIP item covering the full crop through Microsoft Planetary Computer. It extracts a 154-meter square centered on the cell and resamples it to a 256 x 256 PNG. Cells sharing an imagery item are processed together.

Recorded fields include `naip_item`, `naip_year`, `tile_path`, `nodata_frac` and `tile_ok`. Tiles with more than 5% missing pixels are marked unusable. Imagery and street-view years are compared because environmental changes can create input/label mismatches.

### 03: OSM and WorldCover

OSM features use the local Geofabrik Massachusetts snapshot at `raw/osm/massachusetts-latest.osm.pbf`, approximately 300 MB. Downloads are reused and their date and metadata are recorded. This avoids repeated requests to public Overpass servers.

Within the same footprint as each aerial crop, the workflow computes:

- Road lengths by class, cycling infrastructure length, lanes, speed limits and intersections.
- POI counts and water, green-space and building area fractions.
- WorldCover class fractions and the dominant land-cover class.

These form the B1 baseline input and the geographic component of fusion models. Objective features could support additional application capabilities later; they are not automatically part of the current eight-target serving contract.

### 04: teacher labeling

Gemini scores usable street-view images from 1 to 5 on eight dimensions: traffic intensity, quietness, shade, naturalness, scenic appeal, historic character, liveliness and visual openness. It also flags unusable images, such as blurred, night-time or in-vehicle views.

Vertex AI calls use structured JSON and temperature zero. Results are cached by image, prompt version and model; changing labeling rules requires a new prompt version. Usable image scores are averaged per cell and normalized to [0,1]. Cells with fewer than two usable images have missing labels.

The notebook produces an image gallery, score distributions, checks against geographic features, and 150 images with blank forms for human annotation. Human agreement validation remains pending; teacher-generated labels must not be described as human-validated ground truth.

Vertex AI requires an authorized project, billing, the relevant API and credentials, for example ADC configured with `gcloud auth application-default login`. Notebook labeling may incur cloud charges.

### 05: dataset construction

The workflow joins cells, OSM features and labels by `h3_index`. Filtering considers imagery validity, available labels, capture-year differences and missing geographic features.

Splits use H3 resolution-8 spatial blocks, not random rows. Whole blocks are assigned to train, validation and test, with a one-ring buffer near test blocks to reduce leakage. Train/validation blocks also receive five grouped cross-validation folds. A future second city can be held out as an entire test city.

Frozen versions refuse overwrite. Their manifest records input hashes, prompt and OSM versions, filters and split parameters. The final v1 export contains 1,012 cells: 651 train, 173 validation and 188 test.

### 06 and 07: features and baselines

Image extraction and baseline fitting are separated because the research Mac environment has encountered conflicting OpenMP runtimes when importing PyTorch and LightGBM in one process. This also provides a useful boundary for future container jobs.

Frozen image encoders produce embeddings without updating their weights. Eight rotations/flips are averaged for each aerial image and cached, so baseline fitting need not reload image pixels.

- **B0:** mean prediction.
- **B1:** OSM / WorldCover features with LightGBM.
- **M1:** frozen image embeddings with Ridge regression.
- **M3:** PCA-compressed image embeddings fused with geographic features, using LightGBM.

Encoder choice and hyperparameters are selected by five-fold spatially grouped cross-validation on train plus validation. The test set is reserved for final evaluation. Metrics include per-target Spearman correlations and block-bootstrap confidence intervals, including the M3 minus B1 comparison.

## Data layout and identity

```text
data/
├── raw/
│   ├── grid/boston_h3r10.parquet
│   ├── osm/massachusetts-latest.osm.pbf
│   └── mapillary/
│       ├── metadata/boston/tile_z14_*.parquet
│       └── images/<image_id>.jpg
├── processed/
│   ├── cells.parquet
│   ├── streetview.parquet
│   ├── tiles/<cell_id>.png
│   ├── osm_features.parquet
│   ├── labels_image.parquet
│   ├── labels_cell.parquet
│   ├── labels_meta.json
│   ├── labels_gallery.html
│   └── qa/
├── embeddings/v1/
├── results/<run_id>/
├── datasets/v1/
│   ├── dataset.parquet, train.parquet, val.parquet, test.parquet
│   └── schema.json, manifest.json, DATASET_CARD.md
└── runs/<timestamp>/*_manifest.json
```

`h3_index` is the shared geographic key. `cell_id` is derived as `BOS_<h3_index>`. Relative image paths are interpreted from the data root so tables can move across machines and storage locations. Street-view labels and aerial/geographic inputs align through H3 identity.

## Data sources and attribution

| Data | Source | License recorded in the research workflow |
| --- | --- | --- |
| Street-view imagery | Mapillary | CC-BY-SA 4.0; retain attribution and review share-alike obligations for redistribution |
| Aerial imagery | NAIP via Planetary Computer | Public domain |
| Geographic features | OpenStreetMap via Geofabrik | ODbL; attribute OpenStreetMap contributors |
| Land cover | ESA WorldCover 2021 v200 | CC-BY 4.0 |

Collection notebooks 01-03 do not require GCP. Labeling uses paid Vertex AI calls and records usage estimates. This table summarizes existing project documentation; it is not a new license audit.

## Known limitations and next work

- Mapillary vector tiles are a public subset, not guaranteed complete imagery coverage.
- OSM is a dated snapshot; another city needs an appropriate regional file.
- Cross-city generalization is not established by the Boston-only dataset.
- Collection filters are configurable; examine coverage before changing them. Panoramas are currently excluded.
- Complete human label validation before drawing conclusions for each perception target.
- Export reloadable model/preprocessing artifacts, then connect real inference.
- Containerize the data, encoding, training and prediction jobs; provide a reproducible ML command and complete data snapshot.
- Fine-tuning, remote-sensing encoders, winter-filter ablations and a second city are possible research extensions, not requirements already completed.

## Optional upstream acquisition and teacher labeling

Notebook 01-04 logic runs in one `data-source` container with an `all` command;
individual stage commands remain available from the same image. See
[the upstream guide](docs/SOURCE_PIPELINE.md) before acquiring new data or making paid
Vertex AI requests. These jobs do not run as part of `make run`. Original notebooks,
historical GCS inputs and the default training workflow are preserved.

Python containers share a [dependency-management convention](docs/DEPENDENCIES.md):
`pyproject.toml`, committed `uv.lock`, pinned uv and frozen runtime installation.
