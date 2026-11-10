"""Opt-in HTTP stack regression using stored vectors and a mocked Gemini client.

Run after `npm ci && npm run build` in src/frontend and starting Chroma:
RAG_STACK_CHECK=1 CHROMADB_HOST=127.0.0.1 uv run --project src/rag-service pytest src/rag-service/tests/test_stack.py -q
No writes to Chroma, embeddings, manifests, or paid API calls.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest
import uvicorn

pytestmark = pytest.mark.skipif(os.getenv("RAG_STACK_CHECK") != "1", reason="Opt-in live Chroma/Next.js stack check")


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def wait_for(url):
    for _ in range(150):
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            time.sleep(0.1)
    raise AssertionError(f"Service did not start: {url}")


def post(url, data):
    request = Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def test_assistant_proxy_gateway_rag_chroma_and_scoring():
    root = Path(__file__).resolve().parents[2]
    frontend = root / "frontend"
    sys.path.insert(0, str(root / "api-service"))
    import cli
    import service
    from api.service import app as gateway

    rag_port, gateway_port, frontend_port = free_port(), free_port(), free_port()
    assert len({rag_port, gateway_port, frontend_port}) == 3
    env = {"GCP_PROJECT": "", "RAG_GENERATOR": "base", "LLM_PROVIDER": "mock",
           "RAG_SERVICE_URL": f"http://127.0.0.1:{rag_port}", "API_SERVICE_URL": f"http://127.0.0.1:{gateway_port}"}
    cloud = Mock()
    cloud.models.generate_content.return_value = SimpleNamespace(text="Check your brakes before riding.")
    servers, threads = [], []
    process = None
    # Use a real stored vector as the test query vector; never call embedding APIs.
    with patch.dict(os.environ, env), patch.object(cli, "CHROMADB_HOST", os.getenv("CHROMADB_HOST", "127.0.0.1")):
        chroma = cli.chroma_client()
        try:
            collection = chroma.get_collection(service.COLLECTION)
            vector = collection.get(limit=1, include=["embeddings"])["embeddings"][0].tolist()
        finally:
            chroma.close()
        with patch.object(cli, "generate_embeddings", return_value=[vector]) as embed, patch.object(cli, "llm_client", return_value=cloud):
            try:
                for app, port in ((service.app, rag_port), (gateway, gateway_port)):
                    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
                    thread = threading.Thread(target=server.run, daemon=True)
                    servers.append(server)
                    threads.append(thread)
                    thread.start()
                wait_for(f"http://127.0.0.1:{rag_port}/ready")
                wait_for(f"http://127.0.0.1:{gateway_port}/")
                process = subprocess.Popen(
                    [shutil.which("node"), "node_modules/next/dist/bin/next", "start", "-p", str(frontend_port)],
                    cwd=frontend, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                base = f"http://127.0.0.1:{frontend_port}"
                wait_for(base + "/assistant")
                for path, marker in (("/", "Ride Assistant"), ("/assistant", "Message Ride Assistant")):
                    with urlopen(base + path) as response:
                        assert marker in response.read().decode()
                data = post(base + "/api/rag/chat", {"question": "What should I check before riding?"})
                assert data["answer"] == "Check your brakes before riding."
                assert data["sources"][0]["metadata"]["source_id"]
                assert data["sources"][0]["text"]
                assert len(data["sources"]) == 5
                assert embed.call_count == 1
                cloud.models.generate_content.assert_called_once()
                assert "Reference passages:" in cloud.models.generate_content.call_args.kwargs["contents"]
                query = post(f"http://127.0.0.1:{gateway_port}/rag/query", {"question": "How should I prepare?", "n_results": 2})
                assert len(query["sources"]) == 2
                assert query["answer"] is None
                assert embed.call_count == 2
                cloud.models.generate_content.assert_called_once()
                with urlopen(f"http://127.0.0.1:{gateway_port}/rag/ready") as response:
                    assert json.load(response)["ready"] is True
                score = post(f"http://127.0.0.1:{gateway_port}/score", {"text": "I want water views", "region_id": "cambridge"})
                assert score["cells"]
                with pytest.raises(HTTPError) as invalid:
                    post(base + "/api/rag/chat", {"question": " "})
                assert invalid.value.code == 422
                servers[0].should_exit = True
                threads[0].join(timeout=5)
                with pytest.raises(HTTPError) as unavailable:
                    post(base + "/api/rag/chat", {"question": "How should I prepare?"})
                assert unavailable.value.code == 503
                assert post(f"http://127.0.0.1:{gateway_port}/score", {"text": "water", "region_id": "cambridge"})["cells"]
            finally:
                if process is not None:
                    process.terminate()
                    process.wait(timeout=10)
                for server in servers:
                    server.should_exit = True
                for thread in threads:
                    thread.join(timeout=5)
