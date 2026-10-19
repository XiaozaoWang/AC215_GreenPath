FROM python:3.11-slim-bookworm
RUN pip install --no-cache-dir uv==0.12.12
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
COPY src/model-deploy /app/src/model-deploy
COPY src/data-processor /app/src/data-processor
COPY src/api-service/api/dimensions.json /contracts/schema.json
WORKDIR /app/src/data-processor
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
RUN useradd -m -u 1000 app && mkdir /work && chown app:app /work
ENV PATH="/opt/venv/bin:$PATH" HOME=/tmp
USER app
ENTRYPOINT ["greenpath-data"]
CMD ["--help"]
