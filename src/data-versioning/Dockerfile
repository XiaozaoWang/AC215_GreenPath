# The image's "blueprint": system + git + DVC + code. No keys or .env in it, identity is only given to the container at run time
# Difference from the tutorial: no gcsfuse, because we don't mount the bucket, cli.py downloads the files we need instead

# Official Python 3.12 (Debian) slim
FROM python:3.12-slim-bookworm

# git: DVC has to work inside a git repo (it writes the .dvc file and .gitignore)
ARG DEBIAN_PACKAGES="build-essential git curl"

# apt doesn't prompt; use UTF-8; unbuffered logs
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

# uv installs the packages into /.venv (outside /app, so mounting the code doesn't cover it)
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/.venv

# Install system packages and uv; create a normal user app, don't run as root
RUN set -ex; \
    for i in $(seq 1 8); do mkdir -p "/usr/share/man/man${i}"; done && \
    apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends $DEBIAN_PACKAGES && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir --upgrade pip && \
    pip install uv && \
    useradd -ms /bin/bash app -d /home/app -u 1000 && \
    mkdir -p /app && \
    chown app:app /app && \
    mkdir -p /.venv && \
    chown app:app /.venv

USER app

# The repo is mounted from the Mac, so its files aren't owned by app in the container and git refuses by default; this tells git to trust it
RUN git config --global --add safe.directory '*'

WORKDIR /app

# Copy only the dependency files first and install: if deps didn't change this layer comes from cache and build is fast
COPY --chown=app:app pyproject.toml uv.lock* ./
RUN uv sync --frozen

# Then copy the code (data and caches listed in .dockerignore are not copied)
COPY --chown=app:app . ./

# On start, run docker-entrypoint.sh first
ENTRYPOINT ["/bin/bash","docker-entrypoint.sh"]
