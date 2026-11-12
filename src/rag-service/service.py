"""Independent RAG API. Startup/readiness never embed or call Gemini."""
import asyncio
import logging
import os
import json
import time
import uuid
from contextlib import asynccontextmanager
from functools import partial
from typing import Annotated, Literal

import cli
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.responses import JSONResponse

from generators import get_generator

logger = logging.getLogger(__name__)
CHUNK_TYPE = os.getenv("RAG_CHUNK_TYPE", "markdown-split")
if CHUNK_TYPE not in ("char-split", "recursive-split", "markdown-split"):
    raise ValueError("Invalid RAG_CHUNK_TYPE")
COLLECTION = f"greenpath-docs-{CHUNK_TYPE}"
cli.generate_embeddings = partial(cli.generate_embeddings, max_retries=0)
cli.chroma_client = partial(cli.chroma_client, max_attempts=1)


def collection_status():
    client = cli.chroma_client()
    try:
        collection = client.get_collection(name=COLLECTION)
        meta = collection.metadata or {}
        if (meta.get("embedding_model") != cli.EMBEDDING_MODEL
                or meta.get("embedding_dimension") != cli.EMBEDDING_DIMENSION
                or meta.get("hnsw:space") != "cosine"):
            raise ValueError("Collection embedding configuration does not match the RAG service")
        count = collection.count()
        if not count:
            raise ValueError("Collection is empty; load existing embeddings")
        return count
    finally:
        client.close()


@asynccontextmanager
async def lifespan(app):
    # Fail clearly at startup for an unimplemented/unknown generator.
    app.state.generator = get_generator()
    # Bound cloud requests and disable SDK retries. CLI behavior stays unchanged.
    if os.getenv("GCP_PROJECT"):
        cli._client = cli.genai.Client(
            vertexai=True, project=os.environ["GCP_PROJECT"], location=cli.GCP_LOCATION,
            http_options=cli.types.HttpOptions(
                timeout=int(os.getenv("RAG_CLOUD_TIMEOUT_MS", "20000")),
                retry_options=cli.types.HttpRetryOptions(attempts=1),
            ),
        )
    # Database provisioning/restores belong to the Chroma server, not this stateless API.
    # Missing or incompatible collections are reported by /ready; never rebuild them here.
    yield
    if cli._client is not None:
        cli._client.close()
        cli._client = None


app = FastAPI(title="GreenPath RAG", lifespan=lifespan)


@app.middleware("http")
async def request_timeout(request, call_next):
    started = time.perf_counter()
    request_id = uuid.uuid4().hex
    status = 500
    try:
        # Keep a stuck optional dependency from holding the HTTP response open.
        async with asyncio.timeout(4 if request.url.path == "/ready" else 45):
            response = await call_next(request)
    except TimeoutError:
        response = JSONResponse({"detail": "RAG request timed out"}, status_code=504)
    finally:
        # Log only operational fields; questions, passages and credentials stay out of logs.
        if "response" in locals():
            status = response.status_code
        event = {"severity": "ERROR" if status >= 500 else "WARNING" if status >= 400 else "INFO",
                 "message": "rag_request", "request_id": request_id,
                 "method": request.method, "path": request.url.path, "status": status,
                 "duration_ms": round((time.perf_counter() - started) * 1000, 2)}
        trace_id = request.headers.get("x-cloud-trace-context", "").split("/")[0]
        if len(trace_id) == 32 and all(c in "0123456789abcdef" for c in trace_id.lower()) and os.getenv("GCP_PROJECT"):
            event["logging.googleapis.com/trace"] = f"projects/{os.environ['GCP_PROJECT']}/traces/{trace_id}"
        print(json.dumps(event), flush=True)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    n_results: int = Field(default=5, ge=1, le=10, strict=True)


class Source(BaseModel):
    chunk_id: str
    text: str
    distance: float | None
    metadata: dict


class Result(BaseModel):
    question: str
    sources: list[Source]
    answer: str | None = None
    generator: Literal["base", "finetuned"] | None = None


def sources_from(results):
    return [Source(chunk_id=chunk_id, text=document or "", metadata=metadata or {}, distance=distance)
            for chunk_id, document, metadata, distance in zip(
                results["ids"][0], results["documents"][0],
                results["metadatas"][0], results["distances"][0])]


@app.get("/ready")
def ready():
    try:
        return {"ready": True, "collection": COLLECTION, "count": collection_status(),
                "generator": os.getenv("RAG_GENERATOR", "base")}
    except Exception as error:
        logger.warning("RAG readiness failed: %s", error)
        raise HTTPException(503, "RAG knowledge base is unavailable or incompatible") from error


def retrieve(request):
    # Disable long embedding retry loops in the web service only.
    collection_status()
    return cli.retrieve(CHUNK_TYPE, request.question, request.n_results)


def run(request, generate=False):
    try:
        results = retrieve(request)
        answer = app.state.generator.generate(request.question, results) if generate else None
        return Result(question=request.question, sources=sources_from(results), answer=answer,
                      generator=os.getenv("RAG_GENERATOR", "base") if generate else None)
    except Exception as error:
        logger.exception("RAG request failed")
        raise HTTPException(503, "RAG could not complete the request. Please try again later.") from error


@app.post("/query", response_model=Result)
def query(request: Question):
    return run(request)


@app.post("/chat", response_model=Result)
def chat(request: Question):
    return run(request, generate=True)
