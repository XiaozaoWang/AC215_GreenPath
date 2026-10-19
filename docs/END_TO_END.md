# New collection to model to selectable app

## Status

Implementation is provided for user execution. During this change no Docker images were
built, no application was started, no VLM calls/collection/training or tests were run.
Earlier 01-04 and snapshot-based pipeline checks predate the new orchestration/catalog.
Do not treat the new full path as runtime-verified until executing it.

Upstream 01-04 now share one image/service (`data-source`) and run sequentially in one
container. The four downstream ML jobs remain separate. This consolidation awaits user
runtime verification. Per-stage source commands remain available for diagnosis.

See [DATA_LIFECYCLE.md](DATA_LIFECYCLE.md) for storage rules and
[DEPENDENCIES.md](DEPENDENCIES.md) for image builds.

## First inspect the existing results in the new UI

From the repository root:

```sh
make app-versions
```

This registers existing successful pipeline runs, builds the updated API/frontend and
starts the app. It does not collect data, call the teacher or train a model. Frontend is
http://localhost:3000 and API docs are http://localhost:9000/docs.

All application launchers (`make app`, `make app-versions`, `make run` and
`make run-all`) default to frontend port 3000 and API port 9000. Re-running
`make app-versions` recreates this launcher's existing containers when their port
mappings change; no ML jobs are needed. An unrelated process occupying either port
must be stopped first. For an intentional alternate mapping, use
`make app-versions APP_ARGS="--frontend-port 3003 --api-port 9003"`. The pipeline
launchers accept the same flags through `PIPELINE_ARGS` or `WORKFLOW_ARGS`.

The version selector includes the historical fixture and successful registered runs,
such as `serving-local-smoke-001`. Switching versions changes the preview immediately;
input preferences afterward to calculate a match. Refresh versions to discover results
registered since opening the page. No historical outputs are overwritten.

Choose a default without training:

```sh
make app-versions APP_ARGS="--version boston-heldout-en-v2"
make app-versions APP_ARGS="--version serving-local-smoke-001"
```

Only register local results without starting anything:

```sh
make catalog-refresh
python3 tools/manage_artifacts.py list
```

## Credentials

The existing ignored root `.env` remains supported. The host launchers now also read it;
shell environment variables take precedence. Supported interpolation is `$NAME` or
`${NAME}`; use absolute paths for ADC. Shell expressions are never executed.

```dotenv
MAPILLARY_TOKEN=YOUR_CLIENT_TOKEN
GOOGLE_CLOUD_PROJECT=organic-victory-508017-v9
GOOGLE_CLOUD_LOCATION=us-central1
ADC_FILE=/Users/wxz/.config/gcloud/application_default_credentials.json
```

No credentials are printed, written to manifests or copied into images by the launcher.
Mapillary is needed only if acquisition must run. Paid labeling needs Vertex access and
ADC. The downstream online job uses ADC for the versioned CLIP artifact, even when the
source snapshot is local. Offline mode needs already verified caches.

## The supplied pilot configuration

`configs/workflow-boston-small.json` points to the existing 50-cell source configuration
and `.artifacts/sources/boston-source-local-v1`. It can reuse the user's 01-03 outputs and
9 cached labels. It finishes labeling the selected workspace, freezes a new snapshot,
then uses all qualifying pilot cells for training and the map.

The workflow's source paths are explicit config fields. They take precedence over
`SOURCE_CONFIG`/`SOURCE_DATA_DIR` in `.env`; edit/copy the workflow JSON to choose another
source workspace. `configs/training-clip-ridge.json` records the prior CLIP/Ridge alpha
selection so a new collection does not depend on a historical cache directory just to
find training parameters.

The workflow uses `mode: full` for the pilot: all qualifying rows in that collection.
It does not change `sample_size: 50` into city-wide collection. Filtering and spatial
buffering can produce fewer than 50 displayed cells. This is separate from the historical
48-cell smoke recipe, which requires 32/8/8 after splitting.

## Run the complete pilot

```sh
make run-all WORKFLOW_ARGS="--run-id pilot-e2e-001 --allow-labeling"
```

This explicitly permits paid labeling. The config permits at most 200 new images in
one invocation, excluding retries. The current pilot has 192 total street images and
previously had 183 pending; current caches decide the actual work. The command does not
mean 200 dollars, 200 total HTTP attempts or unlimited labeling.

Execution order:

1. Read source config and use the combined source command to validate/reuse 01-03 outputs with hashes.
2. Run the needed acquisition/geographic stages sequentially in one `data-source` container.
3. Run the bounded label batch in that same container; each paid result is persisted immediately.
4. Require labeling completion for the selected workspace before freezing.
5. Freeze local processed source snapshot, keeping label metadata and upstream records.
6. Start the existing four-job pipeline with that exact local snapshot.
7. Prepare spatial splits, encode real images, fit/export and reload/predict.
8. Register the new serving artifact and start API/frontend with it selected by default.
9. Record summaries and the explicit version chain.

Existing verified upstream steps are reused; there is no automatic forced reacquisition.
If any stage fails, downstream steps stop. If there are insufficient spatially separated
labeled cells after filtering, preparation reports the split counts and fails; it does
not weaken the test boundary or switch to historical data. A larger collection then
requires a new source configuration/version/workspace.

Without `--allow-labeling`, incomplete labels cause an explicit stop before acquisition
work starts. A complete cache may be aggregated with zero new calls. An already frozen
matching snapshot skips upstream work entirely. Nothing is uploaded to GCS by run-all.

## Resume

```sh
make run-all WORKFLOW_ARGS="--run-id pilot-e2e-001 --resume --allow-labeling"
```

A resume permits another bounded batch only because the flag is explicit again. There
is no loop that silently exceeds the configured per-invocation label limit. Successful
source outputs are reused. A failed training attempt is retained, and a new training ID
such as `pilot-e2e-001-retry-2` is allocated. A successful prior fit can be reused.
Workflow and actual training run IDs may therefore differ; use the summary's
`training_run_id` for publication.

Other options: `--no-app`, `--no-build`, `--api-port`, `--frontend-port`. Use `--no-build`
only after rebuilding images matching this code. `--offline` requires an existing frozen
snapshot and verified encoder cache; it does not make external collection work offline.

## Use a selected snapshot without 01-04

Historical default, same command as before:

```sh
make run PIPELINE_ARGS="--run-id historical-smoke-002"
```

The frozen new pilot, without collection/labeling:

```sh
make run PIPELINE_MODE=full PIPELINE_ARGS="--run-id pilot-refit-002 --source-dir .artifacts/snapshots/sources/boston-source-local-v1-frozen-v1 --catalog-label Pilot-refit-002"
```

Or select a published snapshot explicitly:

```sh
make run PIPELINE_MODE=full PIPELINE_ARGS="--run-id cloud-refit-001 --source-uri gs://ac215-greenpath/pipeline-inputs/NEW-SNAPSHOT --source-version NEW-SNAPSHOT"
```

Those commands generate new models/results. Frontend version selection only reads
already generated results and does not execute any of them.

## Review and publish to GCS

Inspect `.artifacts/workflows/pilot-e2e-001/workflow-summary.json` and the actual training
run's summary, metrics, serving output and UI before publication. Publish explicitly:

```sh
make publish-run RUN_ID=pilot-e2e-001 PUBLISH_ARGS="--include-upstream"
```

Use a retry training ID if the workflow summary reports one. `--include-upstream` adds
a complete source archive including the paid cache; it requires a new-pipeline snapshot
with a known workspace. Historical snapshots without that lineage can be published
without this option. The publisher refuses to archive source tables changed after freezing.

This uploads versioned artifacts to the existing private bucket and publishes the release
manifest last. It does not merge code, submit coursework or host the app in the cloud.
To retry a partial publication, use the same run, root and include-upstream setting.

Import a serving version published by a teammate or another machine:

```sh
python3 tools/manage_artifacts.py fetch-serving \
  --uri gs://ac215-greenpath/serving/serving-OTHER-RUN \
  --version serving-OTHER-RUN
```

Then refresh versions in the app. GCS directories do not automatically appear in the
local selector: the bundle must first be explicitly downloaded and verified.

## User verification checkpoints

- Historical fixture and prior local run can be selected, with different counts/model IDs.
- Each selection shows its own preview before submitting text, with no teacher call.
- New pilot run reports the new snapshot and a fresh model/serving version.
- The selected map contains the generated pilot cells, not the historical 188-cell region.
- Unknown versions fail rather than falling back; stale responses do not replace a new selection.
- Failed workflow resumes retain old outputs and preserve paid results.
- A published release links the dataset, encoder-referencing model and serving artifact.

Added tests cover per-request version isolation, immutable catalog IDs, preview behavior,
configuration precedence and dataset identity. They are provided but not executed during
this implementation. Existing `make pipeline-test` and `make test` are user-run entry points.
