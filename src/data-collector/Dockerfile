FROM python:3.11-slim-bookworm
RUN pip install --no-cache-dir uv==0.12.12
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib
# Geospatial packages may require a compiler; Rasterio/GDAL also needs Expat at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libexpat1 \
    && rm -rf /var/lib/apt/lists/*
COPY src/data-collector /app/src/data-collector
WORKDIR /app/src/data-collector
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --all-extras
RUN useradd -m -u 1000 app && mkdir /data && chown app:app /data
ENV PATH="/opt/venv/bin:$PATH" HOME=/tmp
USER app
ENTRYPOINT ["greenpath-source"]
CMD ["--help"]
