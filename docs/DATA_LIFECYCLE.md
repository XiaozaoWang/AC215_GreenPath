# Data versions, workflow runs and selectable application results

## Identities and ownership

An execution is not automatically a new dataset. Mutable acquisition work, frozen inputs,
prepared datasets, model runs and application results have different identities.

| Identity | Meaning | Mutation rule |
| --- | --- | --- |
| `source_version` | Acquisition workspace, such as `boston-source-local-v1` | Resume writes only under the same configuration |
| `snapshot_version` | Frozen processed source inputs, such as `boston-source-local-v1-frozen-v1` | Never overwrite |
| `dataset_version` | Prepared data recipe/input identity | Derived from source manifest, contract, recipe and smoke/full mode |
| workflow ID | One source-to-app invocation and its resumes | Summary records progress; stage outputs stay in their own directories |
| training run ID | One fit/encode/predict execution | New directory for each attempt; never overwrite old model artifacts |
| serving version | One geographic prediction artifact | Immutable manifest; catalog pins its SHA-256 |

Prepared dataset IDs now use `dataset-MODE-HASH`. Repeating a fit with identical frozen
inputs, contract and preparation implementation can retain the same dataset identity,
while getting a new model/run identity. Historical `pipeline-smoke-v1`, `v1` and other
existing IDs remain valid; no old directories or manifests are rewritten.

`source_version` in old preparation records referred to the frozen source artifact.
New preparation records keep that compatibility field and additionally record explicit
`snapshot_version` and `source_workspace_version`. The app displays the workspace source
identity when known, the frozen snapshot identity, the prepared dataset and model/run IDs.

## Local storage

```text
.artifacts/
├── sources/<source-version>/         # mutable acquisition, raw images/PBF and paid label cache
├── snapshots/sources/<snapshot-id>/  # immutable processed inputs + NAIP images + provenance
├── source-archives/<snapshot-id>/    # optional complete workspace tar for explicit backup
├── pipeline/
│   ├── cache/                       # verified downloads, encoder weights and extracted images
│   ├── runs/<training-run-id>/
│   │   ├── prepared/
│   │   ├── embeddings/
│   │   ├── model/
│   │   ├── predictions/
│   │   ├── serving/
│   │   ├── logs/
│   │   └── run-summary.json
│   └── latest.json                  # last successful training execution; not dataset identity
├── workflows/<workflow-id>/         # orchestration summary and per-stage logs
├── serving/<imported-version>/      # explicitly downloaded application bundles
├── catalog/
│   ├── serving.json                 # atomic, locked result registry; no copies of scores
│   └── publications/<run-id>.json    # GCS transfer receipts
└── releases/<run-id>/                # immutable publication manifest linking artifacts
```

Existing directories from earlier steps remain where they are. No cleanup or mass
migration is part of this change. New runs use the structure above. `.artifacts` remains
outside Git; code, configs, prompts, locks and documentation belong in Git.

Source workspace images, PBF and label journals are valuable inputs, not disposable
build caches. A processed training snapshot does not contain the full raw workspace.

## GCS publication

Computations use local directories and mounted caches. Reviewed results can be explicitly
published to the existing private bucket; normal training and version selection do not
upload anything automatically.

```text
gs://ac215-greenpath/
├── source-archives/<snapshot-id>/    # opt-in full source workspace backup
├── pipeline-inputs/<snapshot-id>/   # frozen source tables, labels and aerial tiles
├── datasets/<prepared-artifact-id>/
├── features/<embedding-artifact-id>/
├── models/<model-version>/
├── predictions/<prediction-version>/
├── serving/<serving-version>/
└── releases/<release-version>/
```

Each artifact has file sizes/hashes and a manifest published last. Existing differing
objects are rejected, not overwritten. A partial upload can be retried with the same
artifact set. A sealed release records the URIs and manifest hashes of the reviewed run.
Select whether to include the raw archive on the first publication of that release;
changing the release's root or artifact set later is rejected.

The full source archive includes street images and the paid label journal. It is backed
up separately from serving bundles. Retain source attribution and the bucket's existing
private access policy. No credentials belong in any archive. The archive operation holds
the source lock and refuses unfinished hidden/partial files or symbolic links.

The current publisher uses the existing file-based transfer implementation, including
whole-file transfers. This is suitable for the pilot artifacts; very large raw archives
may need chunked/resumable transfer before scaling. This is not cloud-hosted computation.

## Version selection in the application

The API reads a catalog containing relative artifact paths and pinned manifest hashes.
It also exposes the built-in historical 188-cell fixture. Successful local training runs
can be registered without copying or modifying their serving files.

- `GET /versions`: available results and their provenance/coverage.
- `GET /capabilities?version_id=...`: selected contract, dataset, model and region.
- `GET /preview?version_id=...`: saved shade (or first available dimension) preview;
  no preference model or image inference is called.
- `POST /score`: accepts `version_id` and optional `region_id`; uses only that bundle.

The frontend selector changes the saved result. It clears old state, loads a prediction
preview, displays source/snapshot/dataset/model/run and cell counts, then lets the user
submit preferences. Stale responses cannot replace a later selection. A missing or
invalid version is an error, never a fallback to another dataset or fake scores.

Different target contracts are not mixed in one running API. The current catalog requires
bundles matching the active eight-dimensional contract. A future incompatible model
requires a matching API contract/deployment. This preserves validation while allowing
multiple data/model runs of the current task.

Historical held-out predictions and newly trained pilot results have different evaluation
meaning. New maps contain fitting and test cells; a convincing map is not an independent
accuracy measurement. The UI retains that distinction.
