FROM python:3.12-slim-bookworm
RUN pip install --no-cache-dir uv==0.12.12
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1 LANG=C.UTF-8
WORKDIR /app
# Keep dependencies outside /app so the development bind mount cannot hide them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
RUN useradd -m -u 1000 app && mkdir /persistent && chown app:app /persistent /app
COPY --chown=app:app . /app
ENV PATH="/opt/venv/bin:$PATH" HOME=/tmp
USER app
ENTRYPOINT ["/bin/bash", "./docker-entrypoint.sh"]
