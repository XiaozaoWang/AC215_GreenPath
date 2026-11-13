# The image's "blueprint": which system to start from, which software to install, copy the code in, what to do on start
# The image only has the system, dependencies and code, no keys, tokens or .env: identity and secrets are only given to the container at run time

# Official Python 3.12 (Debian) slim
FROM python:3.12-slim-bookworm

ARG DEBIAN_PACKAGES="build-essential curl"

# apt doesn't prompt; use UTF-8; unbuffered logs (so output shows up live on Vertex)
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

# uv installs the packages into /.venv
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
WORKDIR /app

# Copy only the dependency files first and install: if the code changed but deps didn't, this layer comes from cache and build is fast
COPY --chown=app:app pyproject.toml uv.lock* ./
RUN uv sync --frozen

# Then copy the code (results and tests listed in .dockerignore are not copied)
COPY --chown=app:app . ./


# On start, run docker-entrypoint.sh first
ENTRYPOINT ["/bin/bash","docker-entrypoint.sh"]
