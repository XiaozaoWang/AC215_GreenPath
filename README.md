# GreenPath

GreenPath predicts eight ride-experience scores for Boston H3 cells. A Gemini teacher
labels street-view images; the current student uses fixed CLIP aerial-image features
and Ridge regression. The app displays saved predictions through a FastAPI backend and
Next.js frontend. It does not run image inference on each user request.

## Contents

- [1. Setup](#1-setup)
- [2. Credentials](#2-credentials)
- [3. Open the app and select saved results](#3-open-the-app-and-select-saved-results)
  - [Download a shared result from GCS](#download-a-shared-result-from-gcs)
- [4. Train from an existing snapshot](#4-train-from-an-existing-snapshot)
- [5. Run collection through the app](#5-run-collection-through-the-app)
- [6. API and frontend operation](#6-api-and-frontend-operation)
- [7. Check results and stop services](#7-check-results-and-stop-services)
- [8. Publish a completed run to GCS](#8-publish-a-completed-run-to-gcs)
- [Documentation and evidence](#documentation-and-evidence)

| Read | Purpose |
| --- | --- |
| [ML pipeline](docs/ML_PIPELINE.md) | Containers, code folders, notebook mapping and orchestration |
| [Data and model versioning](docs/DATA_AND_MODEL_VERSIONING.md) | What is stored where, input reuse and version selection |
| [Experiments](docs/EXPERIMENTS.md) | Baselines, key settings, results and verification limits |
| [API component](api-service/README.md) / [application notes](docs/app/README.md) | Backend configuration and app context |
| [App design](docs/APP_DESIGN.md) | Wireframe and implementation-driven UI changes |

## Choose a run path

| Command | Starts from | Produces |
| --- | --- | --- |
| `make app-versions` | Existing serving results | Running API/frontend; no training |
| `make run` | Existing snapshot (historical GCS input by default) | New dataset preparation, features, model, predictions and app |
| `make run-all` | Source configuration/workspace, reusing valid outputs | 01-04, frozen snapshot, downstream ML and app |

**Complete pipeline and full-size collection are different.** The default 50-cell pilot
runs the complete source-to-app workflow. `mode: full` uses all qualifying prepared rows
from that pilot. `sample_size: 0` instead selects all eligible cells in the configured
bounding box; it does not mean the entire city.

## 1. Setup

1. Clone the repository and enter the integration branch:

   ```sh
   git clone git@github.com:XiaozaoWang/green-path.git
   cd green-path
   git switch milestone2-integration
   ```

2. Start Docker Desktop. On Linux, use Docker Engine with Compose. Check:

   ```sh
   docker info
   docker compose version
   python3 --version
   make --version
   ```

   Host requirements: Python 3.9+, make and Docker Compose v2+. Allow approximately
   8 GiB of Docker memory. Docker Desktop supplies the local Linux VM; no separate
   cloud VM is required for this run path.

3. Create local configuration only if it does not exist:

   ```sh
   test -f .env || cp .env.example .env
   ```

Python dependencies are installed inside images using pinned uv and committed
`pyproject.toml` / `uv.lock` files. Do not activate the research notebook environment.
The frontend uses Node and `npm ci` inside its container. First builds need internet.

## 2. Credentials

| Operation | Required access |
| --- | --- |
| View bundled or locally saved results | No cloud credentials |
| Download a shared serving result | Your own ADC with bucket read access |
| Online training from a snapshot | ADC with read access to the team's private GCS inputs and encoder |
| New collection and labeling | Mapillary Client Token; Vertex AI project access, billing and ADC |
| Publish artifacts | ADC with write access to the destination bucket |

For the team's authorized GCP project, configure ADC on your machine:

```sh
gcloud auth application-default login
gcloud auth application-default set-quota-project organic-victory-508017-v9
```

Edit the ignored root `.env`; replace the placeholders with your own values:

```dotenv
GOOGLE_CLOUD_PROJECT=organic-victory-508017-v9
GOOGLE_CLOUD_LOCATION=us-central1
ADC_FILE=/absolute/path/to/.config/gcloud/application_default_credentials.json
MAPILLARY_TOKEN=YOUR_MAPILLARY_CLIENT_TOKEN
```

The bucket `gs://ac215-greenpath` is private; ask the team for access. ADC login alone
does not grant bucket or Vertex permissions. Launchers read `.env`; exported shell
variables take precedence. Never commit tokens, ADC files or `.env`.

## 3. Open the app and select saved results

From the repository root:

```sh
make app-versions APP_ARGS="--version boston-heldout-en-v2"
```

Open **http://localhost:3000**; API docs are **http://localhost:9000/docs**.
The historical version contains predictions for **188 held-out test cells**.

Use the frontend **Result version** selector to inspect another locally registered
result. To list IDs and set the default explicitly:

```sh
make catalog-refresh
python3 tools/manage_artifacts.py list
make app-versions APP_ARGS="--version serving-historical-smoke-001"
```

The last command requires the run in section 4 to have succeeded. Without `--version`,
`make app-versions` retains the saved catalog default. Changing the dropdown only changes
the displayed selection. Neither operation retrains a model or overwrites old results.

### Download a shared result from GCS

Obtain the **training run ID**, **serving version ID**, publication root and bucket read
access from the producer. Complete setup and configure your own ADC (section 2).
The result must have been published before downloading. These are example values:

```sh
SHARED_RUN_ID=boston-pilot-v2
SHARED_VERSION=serving-boston-pilot-v2

python3 tools/manage_artifacts.py fetch-serving \
  --run-id "$SHARED_RUN_ID" --version "$SHARED_VERSION"
make app-versions APP_ARGS="--version $SHARED_VERSION"
```

Default publication root: `gs://ac215-greenpath/artifacts`. For another root, add
`--gcs-root gs://YOUR_BUCKET/artifacts` to the download command.
Cloud `pipeline/runs/<run-id>/serving/` restores to the identical relative path under
local `.artifacts/`, and is verified/registered automatically. Open **http://localhost:3000**
and check version/model/cell count against the producer's summary. The example has 49 cells.

Viewing needs only serving, not raw images, model weights or training. `make app-versions`
does not download missing versions. Old explicit `--uri` downloads remain supported;
legacy flat serving URLs use `.artifacts/serving/<version>/`. Bundled fixtures need no cloud
access. Result schemas must match the API/frontend version.

To restore the complete published training run, snapshot and encoder instead:

```sh
make fetch-run RUN_ID="$SHARED_RUN_ID"
# Also restore its raw collection/labeling workspace, if included in the release:
make fetch-run RUN_ID="$SHARED_RUN_ID" FETCH_ARGS="--include-upstream"
```

Restore refuses conflicting local files and can complete a prior serving-only download.
It registers serving without replacing your local catalog/default or running any models.
Source/run/snapshot paths match the cloud layout; encoder weights use a local shared cache.
Workspaces, caches and the catalog on your machine are not overwritten by another user's
catalog or runtime locks.

## 4. Train from an existing snapshot

With GCS credentials configured:

```sh
make run PIPELINE_ARGS="--run-id historical-smoke-001"
```

This skips 01-04, downloads or reuses the historical `boston-processed-v1` snapshot and
CLIP weights, then runs preparation, fresh encoding, training, prediction and the app.
Default smoke size is **32 train + 8 val + 8 test = 48 cells**; the map shows all 48.
Outputs: `.artifacts/pipeline/runs/historical-smoke-001/`.
Use a new run ID for another run; existing run directories are refused.

To use a new local snapshot after section 5 has created it:

```sh
make run PIPELINE_MODE=full PIPELINE_ARGS="--run-id pilot-refit-001 --source-dir .artifacts/snapshots/sources/boston-source-local-v1-frozen-v1"
```

`full` means all qualifying cells in the selected input, not all Boston geography.
For historical full mode, use `make run PIPELINE_MODE=full`.
Add `--no-app` for ML only. Use `--offline` only after all required inputs are cached;
use `--no-build` only when images match the current code.

## 5. Run collection through the app

Review `configs/workflow-boston-small.json` and `configs/source-boston.example.json`, then:

```sh
make run-all WORKFLOW_ARGS="--run-id pilot-e2e-001 --allow-labeling"
```

The configured pilot selects up to 50 cells. One upstream container runs 01-04, reusing
verified outputs, then the launcher freezes inputs, runs the downstream jobs and opens
the app with the new result selected. A matching frozen snapshot skips upstream work.
It never substitutes historical data when the pilot fails.

`--allow-labeling` permits paid requests: the supplied config caps new labeled images at
200 per invocation, excluding retries. Partial results are saved, but incomplete labeling
blocks freezing. Resume explicitly:

```sh
make run-all WORKFLOW_ARGS="--run-id pilot-e2e-001 --resume --allow-labeling"
```

For a fresh collection without reusing the default pilot workspace, generate new paired
configs once (this command does not run the pipeline):

```sh
python3 tools/create_workflow_config.py boston-pilot-v3
make run-all WORKFLOW_CONFIG=configs/workflow-boston-pilot-v3.json WORKFLOW_ARGS="--run-id boston-pilot-v3 --allow-labeling"
```

The generator defaults to 50 cells and 200 new labeled images per invocation. Optional
`--sample-size 0` selects all eligible cells in the same bounding box; `--label-limit N`
sets a positive batch limit. It refuses existing config/data paths. It inherits the
Boston source rules, bbox and training settings; inspect the generated JSON before use.
The new name identifies a new source workspace, snapshot and workflow. Changing only
a run ID does not force recollection. Generate configs once; reuse them for subsequent
invocations. Choose another unused name if `boston-pilot-v3` already exists.

Insufficient spatially separated data can still stop preparation. See
[the pipeline guide](docs/ML_PIPELINE.md) for individual stage commands.
**Verified pilot:** `boston-pilot-v2` completed the consolidated source-to-app workflow
on 2026-10-19: 193 labeled street photos, 50 labeled cells, 49 prepared/predicted cells
(31 train / 10 val / 8 test), and successful API/frontend checks. One cell was removed by
the imagery/street-view year-gap filter. This is a pilot validation, not full-size collection.

## 6. API and frontend operation

`make app-versions` manages both services in Compose project `greenpath-pipeline-app`.
Do not start a second default Compose app on the same ports.

```sh
# Inspect service state and recent logs.
docker compose -p greenpath-pipeline-app -f docker-compose.yml ps
docker compose -p greenpath-pipeline-app -f docker-compose.yml logs --tail=80 api-service frontend

# After editing API/frontend code, rebuild and recreate the app.
make app-versions
```

API: `api-service/api/service.py` attaches the routes. Check `GET /versions`,
`GET /capabilities` and `GET /preview` in Swagger UI. `POST /score` accepts preference
text and an optional `version_id`; Swagger provides the current request schema.
Default preference parsing is offline (`mock`); the displayed vision scores are real
saved model predictions.

Frontend: `frontend/src/app/page.jsx` manages version selection and the map;
`frontend/src/services/DataService.js` calls the API. Select a version, inspect its
preview, then enter a preference such as `A quiet ride with shade, avoid traffic`.
The API reads saved serving data and computes preference matching.

## 7. Check results and stop services

```sh
cat .artifacts/pipeline/runs/historical-smoke-001/run-summary.json
cat .artifacts/workflows/pilot-e2e-001/workflow-summary.json
```

Read only the summary for the command you ran. For run-all retries, use the workflow's
actual `training_run_id` to locate the ML run. Check success, split counts, reload
validation, selected model/version and the frontend's matching cell count.

```sh
docker compose -p greenpath-pipeline-app -f docker-compose.yml down
```

This stops the app without deleting `.artifacts`. If 3000/9000 is occupied, identify and
stop the owning process or explicitly choose alternate launcher ports.

## 8. Publish a completed run to GCS

After reviewing a successful run, publish its saved results. Docker must be running;
ADC needs bucket write access. Use the actual `training_run_id` from the workflow summary
(or the successful `make run` ID). The value below is an example:

```sh
PUBLISH_RUN_ID=boston-pilot-v2
make publish-run RUN_ID="$PUBLISH_RUN_ID" PUBLISH_ARGS="--include-upstream"
```

This uploads existing data without collection, labeling or training. `--include-upstream`
adds the source workspace's actual files under `sources/<source-version>/`, without a tar
wrapper. It requires new-pipeline lineage and unchanged frozen input files. Omit it for
historical snapshots without a source workspace. Publication filters machine locks/hidden
files, keeps stage manifests unchanged and publishes the release last.

Cloud paths under `gs://ac215-greenpath/artifacts/` mirror local formal artifact paths:

| Cloud path | Matching local path under `.artifacts/` |
| --- | --- |
| `sources/<source-version>/` | Same: raw images, geographic inputs, label caches and records |
| `snapshots/sources/<snapshot-version>/` | Same: fixed input package |
| `pipeline/runs/<run-id>/` | Same: prepared, embeddings, model, predictions, serving, logs and summary |
| `releases/mirrored-v1/<run-id>/` | Same: checksummed version/restore links |
| `encoders/<encoder-version>/` | `pipeline/cache/encoders/<encoder-version>/`: reusable weight cache |

To publish to another root, add `GCS_ROOT=gs://YOUR_BUCKET/artifacts` to the Make command.
No catalog, archived data or temporary cache is automatically uploaded; app deployment is
separate. Ordinary `make run` / `make run-all` still do not publish automatically.

```sh
cat ".artifacts/catalog/publications/mirrored-v1/$PUBLISH_RUN_ID.json"
```

The receipt includes serving/release identities and URIs. Retry interrupted uploads with
the same run, root and options; existing different contents are refused. Give viewers the
run ID, serving version, publication root and bucket read access so they can follow
[the download steps](#download-a-shared-result-from-gcs).

Existing legacy GCS prefixes remain as read-compatible backups. Historical standalone
artifacts are copied into `artifacts/reference/`; bundled historical serving results live
under `artifacts/serving/`. The migration script is `tools/migrate_cloud_layout.py`;
without `--apply` it prints a plan only. Migration does not delete old cloud data.

## Documentation and evidence

- [New pilot workflow summary](docs/pipeline/evidence/boston-pilot-v2/workflow-summary.json),
  [run summary](docs/pipeline/evidence/boston-pilot-v2/run-summary.json),
  [upstream log](docs/pipeline/evidence/boston-pilot-v2/01-04_sources.log),
  [ML log](docs/pipeline/evidence/boston-pilot-v2/ml.log),
  [sample request](docs/pipeline/evidence/boston-pilot-v2/sample-request.json) and
  [response](docs/pipeline/evidence/boston-pilot-v2/sample-response.json) record the completed
  source-to-app pilot. The response identifies `serving-boston-pilot-v2` and all 49 map cells.

- [Recorded smoke summary](docs/pipeline/evidence/run-summary.json),
  [ML log](docs/pipeline/evidence/pipeline-ml.log),
  [sample request](docs/pipeline/evidence/sample-request.json),
  [response preview](docs/pipeline/evidence/sample-response-preview.json) and
  [app screenshot](docs/pipeline/evidence/ui-preview.jpg) document an earlier successful run.
- [Wireframe](docs/app/m2-wireframe.svg) and [design notes](docs/APP_DESIGN.md).
  The team still needs to attach its actual M1 feedback/design reference and a current
  screenshot of running local/cloud instances for submission.
- Optional user-run checks: `make source-test`, `make pipeline-test`, `make test`.
  Host component tests require uv and the appropriate Python versions.
- [Research notebooks](docs/notebooks/README.md) are archived references; containers execute
  `src/` scripts. Component READMEs and [dependency details](docs/DEPENDENCIES.md) provide implementation references.

The earlier smoke logs predate consolidation; the new `boston-pilot-v2` evidence covers
the shared upstream image and source-to-app path. Neither run establishes city-wide scale
or strong generalization from eight pilot test cells.
