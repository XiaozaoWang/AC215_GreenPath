# The image "blueprint": which system to start from, what software to install, put the code in, what to do at startup
# The image only has the system, dependencies and code, no keys, tokens or .env: identity and secrets are given to the container only at runtime

# Official Python 3.12 (Debian) slim
FROM python:3.12-slim-bookworm

ARG DEBIAN_PACKAGES="build-essential git curl wget unzip gzip"

# apt doesn't prompt; use UTF-8; unbuffered logs (so output shows live on Vertex)
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

# uv installs packages into /.venv
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/.venv

# install system software and uv; create a normal user app, don't run as root
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
WORKDIR /app

# copy only the dependency list first and install: if the code changed but dependencies didn't, this layer comes from cache and the build is fast
COPY --chown=app:app pyproject.toml uv.lock* ./
RUN uv sync --frozen

# then copy the code (local cache and QC figures listed in .dockerignore are not copied)
COPY --chown=app:app . ./

# put the matplotlib cache in a writable temp dir (.config in the home dir is taken by the mounted gcloud credentials)
ENV MPLCONFIGDIR=/tmp/matplotlib

# run docker-entrypoint.sh at startup
ENTRYPOINT ["/bin/bash","docker-entrypoint.sh"]
