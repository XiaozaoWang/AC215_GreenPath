# Experiments and Verification

## Task and data

The teacher assigns eight street-view perception targets: `traffic_intensity`, `quietness`,
`shade`, `naturalness`, `scenic_appeal`, `historic_character`, `liveliness`, `visual_openness`.
Usable 1-5 scores are aggregated per cell and normalized to [0,1]; fewer than two usable
photos leaves cell targets missing. Labels are Gemini-generated, not human-validated truth.

Historical prepared v1 contains **1,012 cells: 651 train, 173 val, 188 test**. Splitting uses
H3 resolution-8 blocks and a one-ring test buffer over resolution-10 cells, not random rows.
The new acquisition configuration is a separate 50-cell pilot; filtering/buffering can
leave fewer cells or insufficient spatial splits.

## Historical baseline comparison

Source: experiment `baselines-first-run-20261013T0242`, recorded 2026-10-13. The
[original experiment log](pipeline/evidence/historical-experiment-log.md) preserves full
per-target results, block-bootstrap confidence intervals and earlier entries unchanged.

| Model | Inputs / method | Mean test Spearman |
| --- | --- | --- |
| B0 | Constant mean prediction | 0.00 |
| B1 | OSM / WorldCover + LightGBM | 0.53 |
| M1 | Fixed image embeddings + Ridge | 0.44 |
| M3 | PCA image features + geographic features + LightGBM | 0.53 |

Historical grouped CV selected CLIP ViT-B/16; M3 used PCA 32 and LightGBM
`n_estimators=150`, `num_leaves=7`. Fusion did not significantly outperform B1 in any
of the eight targets in the recorded block-bootstrap comparisons. `visual_openness`
was weak (M1 Spearman 0.26). These findings concern agreement with teacher labels.

## Current containerized baseline

M1 provides a simple exportable aerial-image baseline for integration. Its selection
is based on manageable inference/export and prior image-encoder CV, not a claim that it
outperformed the geographic baseline. OSM features remain in preparation/research inputs,
but the deployed Ridge predictor uses CLIP features only.

| Setting | Value / source |
| --- | --- |
| Input image | 256 x 256 aerial PNG; 224 center crop, RGB, CLIP normalization |
| Encoder | Fixed `clip-b16-laion2b-v1`, 512 features; no CLIP fine-tuning |
| TTA | Mean of eight rotation/flip embeddings |
| Regressor | StandardScaler + independent Ridge targets; alpha 1000 |
| Selection | Reuse historical grouped-CV choice; no new search on smoke/test data |
| Final fit | Train + val; held-out test excluded from fitting |
| Configuration | `configs/training-clip-ridge.json`; per-run `prepared/training-config.json` |
| Version records | `model/model.json`, `model/manifest.json`, serving provenance |

Missing target values are handled per target. Read each run's split IDs, metrics and
fit counts rather than assuming a small pilot has the historical dataset size.

## Execution evidence is separate from model quality

| Evidence | What it establishes | Boundary |
| --- | --- | --- |
| Historical experiment above | Baseline comparison on 188 test cells | Teacher agreement, one city |
| Recorded `smoke-20261018-02` | Fresh 48-image encoding, fit/export, independent reload, batch prediction, API/frontend | Historical snapshot input; predates newer changes |
| Earlier individual 01-04 trial | 50 cells, 192 street images, 50 tiles/feature rows; initial partial labeling | Predates uv migration and single-container consolidation |
| `boston-pilot-v2` (2026-10-19) | Consolidated 01-04, new frozen snapshot, downstream training/prediction and app checks | Completed 50-cell pilot; not full-size collection or a generalization benchmark |

The recorded smoke fit **32 train + 8 val** and evaluated **8 test** cells; its map served
all 48 cells. It completed in 92.57 seconds with cached builds. Fresh-process reload error
was approximately **5.96e-8**, below tolerance **1e-6**. This tests execution/reload
consistency; eight test cells are not a reliable new benchmark.

Evidence: [run summary](pipeline/evidence/run-summary.json),
[ML log](pipeline/evidence/pipeline-ml.log),
[container statuses](pipeline/evidence/ml-containers.json),
[smoke metrics](pipeline/evidence/smoke-metrics.json),
[sample request](pipeline/evidence/sample-request.json),
[response preview](pipeline/evidence/sample-response-preview.json),
[UI screenshot](pipeline/evidence/ui-preview.jpg).
Historical logs retain their original 3030/9030 ports; current defaults are 3000/9000.
Old test logs are not evidence that all subsequent revisions passed.

## Completed source-to-app pilot

`boston-pilot-v2` used a fresh source workspace. All three acquisition stages report
`completed`; labeling reports 193 scored/usable photos, zero pending and 50 labeled cells.
The year-gap filter removed one cell, leaving **31 train + 10 val + 8 test = 49**.
Training fitted train + val; test rows were excluded. Fresh-process reload error was
**1.19e-7**, below **1e-6**. The downstream run took 59.65 seconds; this excludes collection
and labeling. The complete workflow lasted about eight minutes.

The saved API response identifies source `boston-pilot-v2`, snapshot
`boston-pilot-v2-frozen-v1`, model `model-boston-pilot-v2` and serving
`serving-boston-pilot-v2`. It includes all 49 map cells, not only the eight held-out cells.
Both workflow and run report success, including API/frontend readiness and result checks.
This demonstrates pipeline execution; it does not replace the historical 188-cell evaluation.

Evidence: [workflow summary](pipeline/evidence/boston-pilot-v2/workflow-summary.json),
[run summary](pipeline/evidence/boston-pilot-v2/run-summary.json),
[label metadata](pipeline/evidence/boston-pilot-v2/labels-meta.json),
[filter counts](pipeline/evidence/boston-pilot-v2/preparation.json),
[reload validation](pipeline/evidence/boston-pilot-v2/validation.json),
[upstream log](pipeline/evidence/boston-pilot-v2/01-04_sources.log),
[ML log](pipeline/evidence/boston-pilot-v2/ml.log),
[request](pipeline/evidence/boston-pilot-v2/sample-request.json) and
[response](pipeline/evidence/boston-pilot-v2/sample-response.json).
The local audit checked sizes and SHA-256 for 37 files across the snapshot and five
artifact directories; all matched their manifests.

## Next iteration and submission evidence

1. Keep the successful pilot as execution evidence. Evaluate larger collections separately;
   record a new source version and preserve spatial test separation when expanding data.
2. Validate teacher labels with human QA, especially weak targets, before investing in
   another encoder, fine-tuning or larger collection. Check imagery/label time gaps.
3. Attach a current running-instance screenshot and confirm saved-result version switching.
   [Wireframe](app/m2-wireframe.svg) and [design changes](APP_DESIGN.md) are available;
   actual M1 feedback must be supplied by the team, not inferred from implementation.

Future research can compare remote-sensing encoders or a second city after label quality
checks. No cloud-hosted app, live per-request image inference, route generation or
cross-city performance is claimed. This update records the user-executed pilot and a
read-only artifact audit; it does not rerun the pipeline or claim new test-suite results.
