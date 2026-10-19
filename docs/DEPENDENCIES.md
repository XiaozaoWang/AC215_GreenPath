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
Dockerfile: pinned uv + uv sync --frozen --no-dev [--all-extras for upstream]
       |
       v
/opt/venv: container-specific Python environment
```

A shared workflow does not mean one shared virtual environment or identical dependency
versions. The API and ML components have different compatibility requirements. Their
locks and environments remain independent, except the four upstream stages deliberately
share one image, environment and project/lock with all four extras installed.

| Container role | Component folder | Python | Lock selection |
| --- | --- | --- | --- |
| 01-04 collection and labeling | `src/data-collector/` | 3.11 | shared lock, `--all-extras`, one `data-source` service |
| 05 data preparation | `src/data-processor/` | 3.11 | own lock, runtime dependencies |
| 06 image encoding | `src/feature-encoder/` | 3.11 | own lock; CPU PyTorch index on Linux |
| 07 baseline training | `src/model-training/` | 3.11 | own lock, runtime dependencies |
| Model reload/prediction | `src/model-deploy/` | 3.11 | own lock, runtime dependencies |
| API | `src/api-service/` | 3.12 | own lock, runtime dependencies |

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
- The upstream Dockerfile installs all four stage extras into one shared image.
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

To install locally, run `uv sync --frozen` in the component directory. For the combined upstream
component, use `uv sync --frozen --all-extras`.
Local development may include the `dev` group where defined; runtime images omit it.

Existing locks in the four downstream ML components and API were retained, avoiding
unrelated library upgrades. Only the upstream lock was newly generated for this migration.

## Scope of this change and verification

The dependency-management convention does not change acquisition, labeling, model training,
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
successful rebuild, select the desired runtime checks from [ML_PIPELINE.md](ML_PIPELINE.md);
do not rerun paid labeling just to check the dependency manager.

## Upstream consolidation

01-04 now use one image (`greenpath-data-source:ml`) and one Compose service
(`data-source`). The `all` command runs these stages sequentially inside one container.
Individual stage commands reuse the same image. This changes image build and startup
configuration, but retains the existing dependency versions and data/version formats.
Rebuild with `make source-build` when source code/dependencies change. Consolidation
was verified by the user-executed `boston-pilot-v2` pilot on 2026-10-19; see
[the current pipeline evidence](ML_PIPELINE.md#evidence-and-current-boundary).
