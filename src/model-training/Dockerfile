FROM python:3.11-slim-bookworm
RUN pip install --no-cache-dir uv==0.12.12
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1 OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
COPY src/model-deploy /app/src/model-deploy
COPY src/model-training /app/src/model-training
WORKDIR /app/src/model-training
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
RUN useradd -m -u 1000 app && mkdir /work && chown app:app /work
ENV PATH="/opt/venv/bin:$PATH" HOME=/tmp
USER app
ENTRYPOINT ["greenpath-train"]
CMD ["--help"]
