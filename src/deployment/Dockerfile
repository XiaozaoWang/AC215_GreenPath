# Image for the deployment tool: just Python and a few packages (read the bucket, call the Cloud Run API).
# Runs on your computer, never in the cloud. No keys or .env inside; credentials are mounted at run time.

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

# uv installs into /.venv, outside /app, so mounting the repo on /app does not hide it
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/.venv

# uv, plus a normal user instead of root
RUN set -ex; \
    pip install --no-cache-dir uv && \
    useradd -ms /bin/bash app -d /home/app -u 1000 && \
    mkdir -p /app /.venv && \
    chown app:app /app /.venv

USER app
WORKDIR /app

# dependencies first, so this layer stays cached when only the code changes
COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --frozen

# code (docker-shell.sh mounts the whole repo on /app anyway, so the latest local code is used)
COPY --chown=app:app . ./

ENTRYPOINT ["/bin/bash","docker-entrypoint.sh"]
