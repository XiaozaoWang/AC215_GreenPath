# API service container

The API keeps its existing Python 3.12 environment, FastAPI application, development
bind mount, entrypoint and port 9000. Its dependency build now follows the same
convention as the ML containers:

- `pyproject.toml` declares runtime dependencies and the `dev` group.
- `uv.lock` records the resolved versions and distribution hashes.
- `.python-version` declares the local Python version.
- `Dockerfile` pins uv 0.12.12 and installs `uv sync --frozen --no-dev` into `/opt/venv`.
- `docker-entrypoint.sh` activates `/opt/venv`, which stays visible when the source
  directory is mounted over `/app`.

Unlike packaged ML command-line components, the API runs `api.service:app` directly;
it does not need a package build backend or a new CLI wrapper merely for uniformity.
Runtime dependencies and the existing lockfile are unchanged by this migration.
The production image omits the development/test dependency group. Host test commands
continue to use the project's own uv environment with development dependencies.

Build from the repository root with the API directory as its context:

```sh
docker build -t greenpath-api-service -f api-service/Dockerfile api-service
```

Existing `make run` and application Compose commands remain the normal launch paths.
See [the shared dependency convention](../docs/DEPENDENCIES.md) and
[the full pipeline guide](../docs/ML_PIPELINE.md) for runtime configuration.

The `boston-pilot-v2` workflow built/started the API and passed application checks on
2026-10-19. See the current pipeline guide for the saved run evidence.

## Saved result versions

`GET /versions` lists registered serving bundles and their provenance. Requests can pass
`version_id` to `/capabilities`, `/preview` and `/score`. Preview is a saved score-dimension
view and never calls the preference provider. Catalog entries are pinned to manifest
hashes; selection is per request, not a mutable global model. Invalid/unknown versions
never fall back to fake scores or another model. See [DATA_AND_MODEL_VERSIONING.md](../docs/DATA_AND_MODEL_VERSIONING.md)
and [ML_PIPELINE.md](../docs/ML_PIPELINE.md).
