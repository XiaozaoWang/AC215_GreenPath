"""Small optional-service gateway; no RAG dependencies in the scoring API."""
import json
import os
import socket
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException
from google.auth.exceptions import GoogleAuthError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from api.model_client import auth_headers

router = APIRouter(prefix="/rag", tags=["rag"])


class RagQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    n_results: int = Field(default=5, ge=1, le=10, strict=True)


def forward(path, body=None):
    base = os.getenv("RAG_SERVICE_URL", "http://rag-api:8001").rstrip("/")
    try:
        # Use the API runtime identity and service URL audience; local HTTP skips tokens.
        headers = {"Content-Type": "application/json", **auth_headers(base)}
    except (GoogleAuthError, OSError) as error:
        raise HTTPException(503, "Unable to authenticate to RAG service") from error
    request = Request(base + path,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers=headers)
    timeout = 5 if path == "/ready" else float(os.getenv("RAG_GATEWAY_TIMEOUT_SECONDS", "50"))
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
            if not isinstance(result, dict):
                raise ValueError("Invalid RAG response")
            return result
    except HTTPError as error:
        status = error.code if error.code in (422, 503, 504) else 502
        raise HTTPException(status, "RAG service could not complete the request") from error
    except (TimeoutError, socket.timeout) as error:
        raise HTTPException(504, "RAG service timed out") from error
    except URLError as error:
        status = 504 if isinstance(error.reason, TimeoutError) else 503
        raise HTTPException(status, "RAG service is unavailable") from error
    except (ValueError, OSError) as error:
        raise HTTPException(502, "Invalid response from RAG service") from error


@router.get("/ready")
def ready():
    return forward("/ready")


@router.post("/query")
def query(request: RagQuestion):
    return forward("/query", request.model_dump())


@router.post("/chat")
def chat(request: RagQuestion):
    return forward("/chat", request.model_dump())
