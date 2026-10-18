FROM python:3.11-slim-bookworm
ARG SOURCE_STAGE=streetview
ENV PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib
# Geospatial dependencies may need a compiler when no architecture-specific wheel exists.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY src/data-source/requirements-*.txt ./
RUN pip install --no-cache-dir -r requirements-${SOURCE_STAGE}.txt
COPY src/data-source /app
COPY prompts /app/prompts
RUN pip install --no-cache-dir --no-deps . \
    && useradd -m -u 1000 app && mkdir -p /data && chown app:app /data
# Use the source copy, including repository prompts, consistently for CLI and runpy modules.
ENV PYTHONPATH=/app
USER app
ENTRYPOINT ["greenpath-source"]
CMD ["--help"]
