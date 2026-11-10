FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/.venv
RUN pip install --no-cache-dir uv==0.12.10 && useradd -m -u 1000 app \
    && mkdir /app /.venv && chown app:app /app /.venv
USER app
WORKDIR /app
COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY --chown=app:app cli.py generators.py service.py ./
CMD ["/.venv/bin/uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8001"]
