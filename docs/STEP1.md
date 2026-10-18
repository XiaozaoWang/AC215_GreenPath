# Step 1: model contract, geographic adapter and GCS artifacts

## What is implemented

- Active model bundle owns the dimension schema, display labels, score bounds and preference examples.
- Default: eight existing model targets and 188 real held-out Boston H3 cells.
- `/capabilities` exposes the active contract and provenance. Restart the API when selecting a different bundle/schema.
- `/score` returns GeoJSON polygon features with raw model scores and preference-weighted values.
- Frontend reads capabilities and renders real geographic polygons. It does not assign table rows to a fake square grid.
- Records and explicitly ordered matrices adapt to a stable `{cell_id, scores}` boundary.
- Missing/duplicate IDs, wrong target sets, invalid polygons, nonfinite/out-of-range values and version mismatches fail validation.
- Unsupported preferences are reported, never silently mapped to another target (e.g. water is not naturalness).
- GCS publish/fetch CLI uses manifests, full SHA256, atomic local caches, generation preconditions and non-overwriting version prefixes.

## Exact scope

This step does **not** refactor/train/export the full model. The fixture uses M1 predictions
from `baselines-first-run-20261013T0242/predictions.parquet`, only `split=test`.
These 188 predictions were produced from the 824 train+validation pool; they do not mix
OOF training predictions with test predictions. No image pixels or teacher labels are
read by the serving adapter. Full future inference needs the encoder/preprocessing and
regression bundle from step 2. Existing notebook GCS_ROOT values are still research
examples; they are not used by the new artifact CLI or proof of a complete upload.

The target schema is `greenpath-experience-en-v2`. `api/dimensions.json` is the authoring
copy; `tools/build_contract_fixture.py` copies it into the release. The API reads the
selected **bundle's** checksummed `schema.json` as its runtime source of truth.
To publish a changed model contract, produce a **new bundle/version**, then restart.
Do not hand-edit the published immutable fixture. Names, descriptions and mock keywords
are metadata; genuine semantic changes require new labels/models, not aliases.

RMSE is not available in the old evaluation artifact. API metadata and UI explicitly
identify 0.05 as an unmeasured placeholder; Spearman/MAE are not relabeled as RMSE.
GeoJSON uses [longitude, latitude]. The current renderer supports Polygon geometry and
a local equirectangular projection for this Boston region. Other geometry types need
a renderer/adapter implementation, not a silent conversion.

## Run the app (no cloud credential required)

From the repository root, with Docker Desktop running:

```sh
docker compose up --build
```

Open http://localhost:3000; API docs: http://localhost:9000/docs.
Default `LLM_PROVIDER=mock` is a keyword stand-in; the geographic predictions are real
saved baseline outputs. `MODEL_PROVIDER=legacy docker compose up --build` explicitly
restores the old simulated 20-dimension grid.

Use `API_PORT=9001 FRONTEND_PORT=3001` if the original app is running. The branch's
validation instance uses Compose project `greenpath-m2-step1` and these alternate ports.

## GCS configuration

Project: `organic-victory-508017-v9`; bucket: `gs://ac215-greenpath` (private, US).
No new service-account key is needed. Host ADC was verified separately.
Each teammate needs their own authorized login/ADC and bucket access; the bucket is
not made public, and no IAM grants to unprovided team accounts have been created.

Published artifact scopes:

| Prefix | Scope |
|---|---|
| `serving/boston-heldout-en-v2` | 188 H3 polygons, eight-dimensional saved test predictions, contract and provenance |
| `datasets/v1-tabular-export-v1` | Frozen dataset/train/val/test tables, original schema/card/manifest and provenance audit; **no image files or raw inputs** |

This is not yet a complete shared training dataset. Raw imagery, encoder weights and
trained regression artifacts still need versioned publication in later steps.

The v1 original manifest's hashes do not match current `processed/cells.parquet` and
`processed/osm_features.parquet`. The original cloud manifest remains intact; the new English-only serving bundle includes
an explicitly translated audit view with its original SHA-256 recorded in provenance. The mismatch
is recorded in `provenance.json`; these current processed files are not passed off as
original v1 inputs. A future complete snapshot must resolve or explicitly version them.

### Fetch and verify on the host

```sh
cd api-service
uv sync --frozen --python 3.12
uv run --frozen python -m api.artifacts fetch \
  gs://ac215-greenpath/serving/boston-heldout-en-v2 ../.artifacts/boston-heldout-en-v2
uv run --frozen python -m api.artifacts verify ../.artifacts/boston-heldout-en-v2
```

Run the app against that downloaded cache:

```sh
MODEL_BUNDLE_DIR=/artifacts/boston-heldout-en-v2 docker compose up --build
```

Set `MODEL_BUNDLE_DIR` to the host cache path for direct, non-Docker Python execution;
the `/artifacts/...` path above is the Compose container mount.
The same CLI can fetch `datasets/v1-tabular-export-v1` into another directory.
A bad existing cache fails with a checksum error. Remove the bad cache explicitly or
choose a clean directory; the tool does not silently replace versioned files.

### Container ADC (opt-in)

```sh
LLM_PROVIDER=gemini docker compose -f docker-compose.yml -f docker-compose.gcp.yml up --build
```

This mounts `${HOME}/.config/gcloud/application_default_credentials.json` read-only.
Override `ADC_FILE` for a different host path. A missing file fails rather than creating
a fake credential directory. Credentials are neither copied into an image nor committed.
For an authenticated one-shot container artifact command (writable cache; API stays read-only):

```sh
docker compose -f docker-compose.yml -f docker-compose.gcp.yml run --rm artifact-tools \
  fetch gs://ac215-greenpath/serving/boston-heldout-en-v2 /artifacts/boston-heldout-en-v2
```

### Publish a new prepared version

```sh
cd api-service
uv run --frozen python -m api.artifacts publish gs://ac215-greenpath/serving/NEW-VERSION /path/to/prepared-artifact
```

The directory must contain a valid manifest with `artifact_version` and a `files` map
of `{relative_path: {sha256, size_bytes}}`. Every serving file must be listed.
Data objects are uploaded before the manifest. Generation-match-zero prevents replacing
an existing object; identical uploads are idempotent, conflicting versions are rejected.
Updating the bucket does not automatically change the active API bundle: select the
new version explicitly and restart. No routine endpoint needs GCS network access.

## Model output formats

Records (manifest `model_output.format=records`, field names configurable):

```json
[{"cell_id": "BOS_<h3>", "scores": {"shade": 0.5, "...": 0.6}}]
```

Matrix (manifest `model_output.format=matrix`):

```json
{"ids": ["BOS_<h3>"], "dimensions": ["shade", "..."], "values": [[0.5, 0.6]]}
```

These are abbreviated examples; the complete active dimension set is mandatory.
Column order is explicit and verified. No extra score is invented to fill missing data.
The geographic file is a GeoJSON FeatureCollection with matching feature `id`,
`properties.cell_id`, `properties.h3_index` and closed Polygon coordinates.

## Tests and fixture reconstruction

```sh
cd api-service
uv sync --frozen --python 3.12
uv run --frozen pytest -q
```

Tests cover scoring, preference retry/fallback, unsupported concepts, model alignment,
records/matrix adapters, broken/version-mismatched artifacts, path traversal, immutable
publication and atomic failed downloads. Cloud-independent tests use an in-memory store.
Cloud round-trip verification is documented in `step1/evidence/`.

To reproduce the fixture from the existing data-notebook environment (pandas/pyarrow/h3):

```sh
python tools/build_contract_fixture.py --data-root /path/to/original/data \
  --output /path/to/NEW-fixture --snapshot-output /path/to/NEW-tabular-export
```

The script refuses to overwrite an existing output. It does not label, train or select a
new model. The source dataset/experiment and file hashes are retained for audit.

## Next steps

1. Export reloadable model/preprocessing artifacts (step 2).
2. Generate full-region predictions with that model, preserving held-out evaluation.
3. Containerize data/encoding/training and compose a genuine ML pipeline.
4. Publish a complete data snapshot and collect clean-clone Milestone 2 evidence.

The current `docker compose up` runs the application, **not** the full data-to-training pipeline.

## Verified in this branch

- Python 3.12 Docker environment: **59 tests passed**.
- API and frontend containers started on isolated ports **9001 / 3001**.
- Final frontend production Docker build passed.
- Real browser confirmed H3 placement, dynamic dimensions and unsupported-water notice.
- Host and authenticated one-shot container previously fetched and verified the original `boston-heldout-v1` GCS bundle.
- Those original cloud checks do not establish publication or container download of the new English-only revision.
- API artifact mounts are read-only.
- Evidence: `docs/step1/evidence/verification-summary.json`, API request/response, test/build logs and screenshot.

## English-only revision

The active contract is `greenpath-experience-en-v2`; the serving artifact is
`boston-heldout-en-v2`. Labels, offline keywords, examples and documentation are English.
The offline parser now recognizes English keywords only. Predictive scores, geographic
boundaries and model version are unchanged. No notebook was run or translated.

The original GCS `serving/boston-heldout-v1` and `datasets/v1-tabular-export-v1` objects
are historical artifacts and remain unchanged, including their original metadata language.
Never overwrite those prefixes to translate their content. The translated source dataset
manifest in the new bundle changes funnel key labels only; provenance retains the original
file SHA-256. Its source keys are recognized with Unicode escapes in the exporter so
historical datasets can be read without changing their files.

The English-only serving revision has been published to its new GCS prefix and fetched
and verified on the host. See `step1/evidence/english-revision.json` for validation status. Earlier build logs and the screenshot describe the original step-1 verification;
they are retained as historical evidence rather than presented as fresh runs.

The local validation instance now reads `/artifacts/boston-heldout-en-v2`. Its live
`/capabilities` reports the English schema and all 59 tests passed again inside Docker.
The new bundle was fetched with host ADC; a separate container-ADC download was not
repeated for this revision.
