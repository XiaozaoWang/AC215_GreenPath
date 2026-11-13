"""Send H3 batches to the private Model Service and return results in input order."""
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import BoundedSemaphore
from urllib.parse import urlsplit

import httpx
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from api.schemas import Tile, TileScore

MAX_BATCHES = 30          # one request is split into at most 30 batches
MIN_BATCH_SIZE = 10       # but each batch has at least 10 cells: anything from 300 cells up still gets 30 batches (fastest),
                          # only a tiny area (e.g. 30 cells) is not split into 1-cell batches that each wake an instance
MAX_BATCH_SIZE = 2000
REQUEST_TIMEOUT = 300
MAX_CONCURRENCY = int(os.getenv("MODEL_MAX_CONCURRENCY", "10"))
if not 1 <= MAX_CONCURRENCY <= 100:
    raise ValueError("MODEL_MAX_CONCURRENCY must be between 1 and 100")
# All /score requests in this process share the same outgoing request budget.
# With one API instance and a budget of 100, three requests (30 batches each) run fully in parallel.
_request_slots = BoundedSemaphore(MAX_CONCURRENCY)

# Retry when Cloud Run cannot hand the batch to an instance yet. On a cold Model Service many instances start at once
# (about 40 s each); Cloud Run answers some of the waiting requests with 500 "aborted after waiting too long for an
# available instance", 429 or 503. Retrying with backoff until the instances are up turns these into successes.
# Only our own waiting between tries counts, not the time a request spends in Cloud Run's queue: on 11-13 a batch
# sat in the queue for 129 s before its 500, and a limit on total time left it no retry. The waits add up to about
# 90 s, longer than a cold start, so a batch that is rejected right away (as on 11-11) also outlasts the start.
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_WAITS = (2, 4, 8, 15, 15, 15, 15, 15)    # seconds before each retry: up to 8 retries, 89 s of waiting in total
_sleep = time.sleep


class ModelServiceError(RuntimeError):
    pass


class ModelTimeoutError(ModelServiceError):
    pass


def split_batches(tiles: list[Tile]) -> list[list[Tile]]:
    # Integer boundaries give balanced, contiguous batches: at most 30 batches, at least 10 cells each (when there are
    # that many), at most 2000 cells each. Boston (2098) -> 30 x ~70; 700 -> 30 x ~23; 300 -> 30 x 10; 100 -> 10 x 10.
    size = len(tiles)
    if size > MAX_BATCHES * MAX_BATCH_SIZE:
        raise ModelServiceError("Region exceeds the Model Service limit of 60000 cells")
    if not size:
        return []
    count = min(MAX_BATCHES, -(-size // MIN_BATCH_SIZE))
    return [tiles[i * size // count:(i + 1) * size // count] for i in range(count)]


def post_with_retry(client: httpx.Client, url: str, body: dict) -> httpx.Response:
    # Retry 429/5xx after each wait in RETRY_WAITS; any other answer, or the last try, returns at once.
    for wait in (*RETRY_WAITS, None):
        response = client.post(url, json=body)
        if response.status_code not in RETRY_STATUSES or wait is None:
            return response
        _sleep(wait)


def auth_headers(base_url: str) -> dict[str, str]:
    # Local HTTP proxies supply authentication themselves; Cloud Run HTTPS needs an ID token.
    if base_url.startswith("http://"):
        return {}
    token = id_token.fetch_id_token(Request(), base_url)
    return {"Authorization": f"Bearer {token}"}


def predict(tiles: list[Tile], on_batch=None) -> list[TileScore]:
    """Predict all tiles; the result keeps the input order.

    on_batch(rows), when given, is called from the calling thread with each batch as soon as it has finished (in completion
    order), so callers can show progress. Batches are contiguous slices of tiles, so ordering the tiles by priority
    makes the first batches the most important ones."""
    parts = split_batches(tiles)
    if not parts:
        return []
    url = os.getenv("MODEL_SERVICE_URL", "").strip().rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc
            or parsed.path or parsed.query or parsed.fragment):
        raise ModelServiceError("MODEL_SERVICE_URL must be the Model Service base URL")

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, headers=auth_headers(url)) as client:
            def call(part):
                with _request_slots:
                    response = post_with_retry(client, f"{url}/predict", {"h3_indices": [t.h3_index for t in part]})
                response.raise_for_status()
                # Required fields come from the handoff; TileScore validates status and all 15 scores.
                rows = [TileScore(h3_index=row["h3_index"], status=row["status"], scores=row["scores"])
                        for row in response.json()["results"]]
                if [row.h3_index for row in rows] != [tile.h3_index for tile in part]:
                    raise ModelServiceError("Model results must match requested H3 cells in count and order")
                return rows

            # The result list follows submission order even when batches finish in a different order.
            with ThreadPoolExecutor(max_workers=min(len(parts), MAX_CONCURRENCY)) as pool:
                futures = [pool.submit(call, part) for part in parts]
                if on_batch:
                    for future in as_completed(futures):
                        on_batch(future.result())
                return [row for future in futures for row in future.result()]
    except (GoogleAuthError, OSError) as exc:
        raise ModelServiceError("Unable to obtain a Model Service ID token") from exc
    except httpx.TimeoutException as exc:
        raise ModelTimeoutError("Model Service prediction timed out") from exc
    except httpx.HTTPError as exc:
        raise ModelServiceError("Model Service prediction request failed") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise ModelServiceError("Invalid Model Service prediction response") from exc
