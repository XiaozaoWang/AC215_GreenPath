# Shared Python container dependency convention

## One installation workflow, separate environments

All Python containers use the same dependency-management process:

```text
pyproject.toml: direct dependencies, optional stage extras and development groups
       |
       v
uv.lock: resolved direct/transitive versions and distribution hashes
       |
       v
Dockerfile: pinned uv + uv sync --frozen --no-dev [--extra STAGE]
       |
       v
/opt/venv: container-specific Python environment
```

A shared workflow does not mean one shared virtual environment or identical dependency
versions. The API and ML components have different compatibility requirements. Their
locks and environments remain independent, except the four upstream stages deliberately
share one project/lock and select different extras.

| Container role | Component folder | Python | Lock selection |
| --- | --- | --- | --- |
| 01 streetview | `src/data-source/` | 3.11 | same project lock, `streetview` extra |
| 02 aerial tiles | `src/data-source/` | 3.11 | same project lock, `tiles` extra |
| 03 geographic features | `src/data-source/` | 3.11 | same project lock, `features` extra |
| 04 teacher labeling | `src/data-source/` | 3.11 | same project lock, `labeling` extra |
| 05 data preparation | `src/data-processor/` | 3.11 | own lock, runtime dependencies |
| 06 image encoding | `src/feature-encoder/` | 3.11 | own lock; CPU PyTorch index on Linux |
| 07 baseline training | `src/model-training/` | 3.11 | own lock, runtime dependencies |
| Model reload/prediction | `src/model-deploy/` | 3.11 | own lock, runtime dependencies |
| API | `api-service/` | 3.12 | own lock, runtime dependencies |

Every component folder has these recognizable files:

```text
component/
├── Dockerfile
├── .python-version
├── pyproject.toml
├── uv.lock
├── README.md
└── Python source package(s)
```

The API retains `api/` and `docker-entrypoint.sh`; the ML packages expose command-line
entry points. These differences follow their roles. No source directories were moved
solely to make the tree identical. Tests remain where each component already stores them.

## Docker build rules

- Pin uv to 0.12.12 in every Python image.
- Use `UV_PROJECT_ENVIRONMENT=/opt/venv`, `UV_LINK_MODE=copy` and
  `UV_PYTHON_DOWNLOADS=never`. Put `/opt/venv/bin` on `PATH`.
- Install from committed locks with `uv sync --frozen --no-dev`.
- The upstream Dockerfile additionally selects one explicit stage extra.
- Keep the environment outside bind-mounted source/data folders.
- Use a non-root runtime user; bind-mounted writable data uses the host UID/GID.
- Mount data, weights and credentials at runtime; do not package them as dependencies.

The `pip install uv==0.12.12` bootstrap installs the package manager itself. Application
libraries are installed by uv from the lock, not from unversioned pip install commands.

Python locks do not include Debian system libraries or model weights. The upstream image
still installs `libexpat1` for Rasterio/GDAL, plus compiler tools where source builds need
them. CLIP checkpoints remain separate versioned artifacts. Debian base tags and apt
packages are not a promise of byte-for-byte immutable OS builds.

## Dependency changes

Edit the relevant `pyproject.toml`, then run `uv lock` in that component directory.
Review and commit the declaration and lock together. Do not hand-edit the lock, update
it during application startup or introduce a parallel requirements file.

To install locally, run `uv sync --frozen` in the component directory. For an upstream
stage, additionally select its extra, for example `uv sync --frozen --extra tiles`.
Local development may include the `dev` group where defined; runtime images omit it.

Existing locks in the four downstream ML components and API were retained, avoiding
unrelated library upgrades. Only the upstream lock was newly generated for this migration.

## Scope of this change and verification

The dependency-management change does not change acquisition, labeling, model training,
API routes, ports, source-data versions or user command names. It does change image build
inputs, the upstream code path inside its images and the API virtual environment path.
The matching prompt location and API entrypoint have been adjusted accordingly.

The user previously exercised the four upstream stages and the snapshot-based training
pipeline. Those results predate this migration. During this change, only dependency
metadata resolution was performed to generate the upstream lock. No Docker image build,
application startup, tests, collection, VLM request or training was performed.

For user verification, first rebuild the upstream and API images without starting tasks:

```sh
make source-build
docker compose -f docker-compose.yml build api-service
```

The downstream ML Dockerfiles did not change. To also rebuild them, supply the variables
required by their Compose file, even for a build-only invocation:

```sh
PIPELINE_CACHE_DIR="$PWD/.artifacts/pipeline/cache" \
PIPELINE_RUNS_DIR="$PWD/.artifacts/pipeline/runs" \
PIPELINE_RUN_ID=build-only \
docker compose -f docker-compose.ml.yml build
```

Image builds can download packages but do not invoke data or model commands. After a
successful rebuild, select the desired runtime checks from `SOURCE_PIPELINE.md` and
`PIPELINE.md`; do not rerun paid labeling just to check the dependency manager.
