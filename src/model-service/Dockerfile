# Image of the Model Service: deployed to Cloud Run, runs all the time, scores when a request comes in
# The image has: system + Python packages + code + model (original DINOv2 weights + our trained weights in artifacts/)
# No keys or .env inside: on Cloud Run it reads the bucket with the service account attached to the service, locally with the mounted gcloud login

# Official Python 3.12 (Debian) slim
FROM python:3.12-slim-bookworm

# apt doesn't prompt; use UTF-8; unbuffered logs (so output shows up live on Cloud Run)
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

# uv installs packages into /.venv; the original DINOv2 weights download to /hf (the Hugging Face cache folder)
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/.venv
ENV HF_HOME=/hf

# Install uv; make a normal user app, don't run as root (only ready-made Python packages, no need for apt to install system software)
RUN set -ex; \
    pip install --no-cache-dir uv && \
    useradd -ms /bin/bash app -d /home/app -u 1000 && \
    mkdir -p /app /.venv /hf && \
    chown app:app /app /.venv /hf

USER app
WORKDIR /app

# Copy only the dependency list first and install: when deps haven't changed this layer comes from cache, so the build is fast
# --no-cache: don't keep uv's download cache in the image (about 1 GB), smaller image, Cloud Run pulls it faster on cold start
COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache

# Download the original DINOv2 weights (about 90 MB) into the image at build time: no download at startup, faster cold start, and no dependence on the Hugging Face site
# The model name is read from network.py (the same one training uses), so copy only that first
COPY --chown=app:app artifacts/network.py artifacts/
RUN /.venv/bin/python -c "import timm; from artifacts import network; timm.create_model(network.SOURCE, pretrained=True)"
ENV HF_HUB_OFFLINE=1

# Then copy the code and the other model files (best_model.pt, config.json)
COPY --chown=app:app . ./

# At startup run docker-entrypoint.sh first
ENTRYPOINT ["/bin/bash","docker-entrypoint.sh"]
