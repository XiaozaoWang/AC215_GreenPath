"""Optional gateway regression coverage; all upstream requests are mocked."""
import io
import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.auth.exceptions import DefaultCredentialsError

from api import model_client
from api.routers import rag

app = FastAPI()
app.include_router(rag.router)
client = TestClient(app)


@pytest.fixture(autouse=True)
def local_rag(monkeypatch):
    monkeypatch.delenv("RAG_SERVICE_URL", raising=False)


def test_gateway_validation_prevents_upstream_calls():
    with patch.object(rag, "urlopen") as upstream:
        for body in ({"question": " "}, {"question": "x" * 2001}, {"question": "How?", "n_results": 11}):
            assert client.post("/rag/chat", json=body).status_code == 422
    upstream.assert_not_called()


def test_gateway_forwards_request_and_sources():
    result = {"answer": "Check brakes.", "sources": [{"metadata": {"source_id": "GP-TEST"}}]}
    with patch.object(rag, "urlopen", return_value=io.BytesIO(json.dumps(result).encode())) as upstream:
        response = client.post("/rag/chat", json={"question": " How? "})
    assert response.json() == result
    request = upstream.call_args.args[0]
    assert request.full_url.endswith("/chat")
    assert json.loads(request.data) == {"question": "How?", "n_results": 5}
    assert upstream.call_args.kwargs["timeout"] == 50


def test_gateway_handles_outage_timeout_bad_response_and_upstream_error():
    for error, status in ((URLError("offline"), 503), (TimeoutError(), 504),
                          (HTTPError("url", 503, "offline", {}, None), 503)):
        with patch.object(rag, "urlopen", side_effect=error):
            assert client.get("/rag/ready").status_code == status
    with patch.object(rag, "urlopen", return_value=io.BytesIO(b"invalid")):
        assert client.get("/rag/ready").status_code == 502


def test_query_and_readiness_paths():
    with patch.object(rag, "forward", return_value={"ready": True}) as forward:
        assert client.get("/rag/ready").status_code == 200
        forward.assert_called_once_with("/ready")
    with patch.object(rag, "forward", return_value={"sources": []}) as forward:
        assert client.post("/rag/query", json={"question": "How?"}).status_code == 200
        forward.assert_called_once_with("/query", {"question": "How?", "n_results": 5})


@pytest.mark.parametrize("base", ["http://rag-api:8001", "https://rag.run.app/"])
@pytest.mark.parametrize("path", ["/chat", "/query", "/ready"])
def test_gateway_authenticates_https_only(monkeypatch, base, path):
    monkeypatch.setenv("RAG_SERVICE_URL", base)
    result = {"ready": True} if path == "/ready" else {"answer": "Check brakes.", "sources": []}
    with patch.object(model_client.id_token, "fetch_id_token", return_value="rag-token") as token, \
            patch.object(rag, "urlopen", return_value=io.BytesIO(json.dumps(result).encode())) as upstream:
        response = client.get("/rag" + path) if path == "/ready" else client.post(
            "/rag" + path, json={"question": "How?"})
    assert response.status_code == 200 and response.json() == result
    request = upstream.call_args.args[0]
    assert request.full_url == base.rstrip("/") + path
    assert request.get_header("Content-type") == "application/json"
    if base.startswith("https://"):
        token.assert_called_once()
        assert token.call_args.args[1] == base.rstrip("/")
        assert request.get_header("Authorization") == "Bearer rag-token"
    else:
        token.assert_not_called()
        assert request.get_header("Authorization") is None
    assert upstream.call_args.kwargs["timeout"] == (5 if path == "/ready" else 50)


def test_gateway_token_failure_is_safe(monkeypatch):
    monkeypatch.setenv("RAG_SERVICE_URL", "https://rag.run.app")
    with patch.object(model_client.id_token, "fetch_id_token", side_effect=DefaultCredentialsError("private detail")), \
            patch.object(rag, "urlopen") as upstream:
        response = client.post("/rag/chat", json={"question": "How?"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Unable to authenticate to RAG service"}
    upstream.assert_not_called()
