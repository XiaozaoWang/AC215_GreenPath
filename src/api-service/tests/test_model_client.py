import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import httpx
import pytest
from google.auth.exceptions import DefaultCredentialsError

from api import model_client
from api.dimensions import DIMENSION_NAMES
from api.schemas import Tile


def row(cell, status="ok"):
    return {"h3_index": cell, "status": status,
            "scores": dict.fromkeys(DIMENSION_NAMES, 0.5) if status == "ok" else None}


@pytest.fixture
def transport(monkeypatch):
    # Replace only the HTTP transport; exercise real request construction and validation.
    client = httpx.Client
    monkeypatch.setenv("MODEL_SERVICE_URL", "http://localhost:8080")
    def install(handler):
        monkeypatch.setattr(model_client.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    return install


@pytest.mark.parametrize("size", [0, 1, 31, 60000])
def test_balanced_batches(size):
    tiles = [Tile(h3_index=str(i)) for i in range(size)]
    parts = model_client.split_batches(tiles)
    assert [tile for part in parts for tile in part] == tiles
    assert len(parts) == min(size, 30)
    if parts:
        lengths = [len(part) for part in parts]
        assert max(lengths) <= 2000 and max(lengths) - min(lengths) <= 1
    if size == 60000:
        with pytest.raises(model_client.ModelServiceError):
            model_client.split_batches([Tile(h3_index="a")] * 60001)


def test_order_duplicates_missing_cells_and_shared_concurrency(transport, monkeypatch):
    # Two simultaneous score calls must share the same outgoing request limit.
    monkeypatch.setattr(model_client, "MAX_CONCURRENCY", 2)
    monkeypatch.setattr(model_client, "_request_slots", threading.BoundedSemaphore(2))
    lock, started, release = threading.Lock(), threading.Event(), threading.Event()
    active = peak = 0
    def handler(request):
        nonlocal active, peak
        ids = json.loads(request.content)["h3_indices"]
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                started.set()
        assert release.wait(5)
        with lock:
            active -= 1
        return httpx.Response(200, json={"results": [row(cell, "not_downloaded" if cell == "missing" else "ok") for cell in ids]})
    transport(handler)
    tiles = [Tile(h3_index=str(i)) for i in range(31)] + [Tile(h3_index="missing"), Tile(h3_index="0")]
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(model_client.predict, tiles) for _ in range(2)]
        try:
            assert started.wait(5)
        finally:
            release.set()
        for future in futures:
            predictions = future.result()
            assert [p.h3_index for p in predictions] == [t.h3_index for t in tiles]
            assert predictions[-2].status == "not_downloaded" and predictions[-2].scores is None
    assert peak == 2


@pytest.mark.parametrize("results", [[], [row("wrong")], [row("a"), row("a")],
    [{"h3_index": "a", "scores": {}}], [row("a") | {"scores": {}}]])
def test_invalid_results(transport, results):
    transport(lambda request: httpx.Response(200, json={"results": results}))
    with pytest.raises(model_client.ModelServiceError):
        model_client.predict([Tile(h3_index="a")])


@pytest.mark.parametrize("failure,expected", [(503, model_client.ModelServiceError),
    (httpx.ConnectError("offline"), model_client.ModelServiceError),
    (httpx.ReadTimeout("slow"), model_client.ModelTimeoutError), ("bad json", model_client.ModelServiceError)])
def test_upstream_failures(transport, failure, expected):
    def handler(request):
        if isinstance(failure, Exception):
            raise failure
        return httpx.Response(failure) if isinstance(failure, int) else httpx.Response(200, text=failure)
    transport(handler)
    with pytest.raises(expected):
        model_client.predict([Tile(h3_index="a")])


def test_https_authentication_and_failure(transport, monkeypatch):
    monkeypatch.setenv("MODEL_SERVICE_URL", "https://model.run.app/")
    token = Mock(return_value="test-token")
    monkeypatch.setattr(model_client.id_token, "fetch_id_token", token)
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.extensions["timeout"]["read"] == 300
        return httpx.Response(200, json={"results": [row("a")]})
    transport(handler)
    assert model_client.predict([Tile(h3_index="a")])[0].status == "ok"
    assert token.call_args.args[1] == "https://model.run.app"
    token.side_effect = DefaultCredentialsError("no ADC")
    with pytest.raises(model_client.ModelServiceError, match="ID token"):
        model_client.predict([Tile(h3_index="a")])


def test_on_batch_reports_every_batch_and_the_result_keeps_input_order(transport):
    def handler(request):
        ids = json.loads(request.content)["h3_indices"]
        return httpx.Response(200, json={"results": [row(i) for i in ids]})

    transport(handler)
    seen = []
    tiles = [Tile(h3_index=str(i)) for i in range(70)]
    result = model_client.predict(tiles, on_batch=seen.append)
    assert [r.h3_index for r in result] == [t.h3_index for t in tiles]
    assert len(seen) == 30 and sum(len(batch) for batch in seen) == 70
