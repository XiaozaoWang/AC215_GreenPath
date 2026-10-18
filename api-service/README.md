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
[the full pipeline guide](../docs/PIPELINE.md) for runtime configuration.

The updated image has not been built or run during this dependency change; user-run
verification is still needed. Previously recorded API results predate this Dockerfile.
