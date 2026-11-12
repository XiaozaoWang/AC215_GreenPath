"""Send H3 batches to the private Model Service and return results in input order."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from urllib.parse import urlsplit

import httpx
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from api.schemas import Tile, TileScore

MAX_BATCHES = 30
MAX_BATCH_SIZE = 2000
REQUEST_TIMEOUT = 300
MAX_CONCURRENCY = int(os.getenv("MODEL_MAX_CONCURRENCY", "3"))
if not 1 <= MAX_CONCURRENCY <= MAX_BATCHES:
    raise ValueError("MODEL_MAX_CONCURRENCY must be between 1 and 30")
# All /score requests in this process share the same outgoing request budget.
_request_slots = BoundedSemaphore(MAX_CONCURRENCY)


class ModelServiceError(RuntimeError):
    pass


class ModelTimeoutError(ModelServiceError):
    pass


def split_batches(tiles: list[Tile]) -> list[list[Tile]]:
    # Integer boundaries give balanced, contiguous batches, each at most 2000 cells.
    size = len(tiles)
    if size > MAX_BATCHES * MAX_BATCH_SIZE:
        raise ModelServiceError("Region exceeds the Model Service limit of 60000 cells")
    if not size:
        return []
    count = min(MAX_BATCHES, size)
    return [tiles[i * size // count:(i + 1) * size // count] for i in range(count)]


def auth_headers(base_url: str) -> dict[str, str]:
    # Local HTTP proxies supply authentication themselves; Cloud Run HTTPS needs an ID token.
    if base_url.startswith("http://"):
        return {}
    token = id_token.fetch_id_token(Request(), base_url)
    return {"Authorization": f"Bearer {token}"}


def predict(tiles: list[Tile]) -> list[TileScore]:
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
                    response = client.post(f"{url}/predict", json={"h3_indices": [t.h3_index for t in part]})
                response.raise_for_status()
                # Required fields come from the handoff; TileScore validates status and all 15 scores.
                rows = [TileScore(h3_index=row["h3_index"], status=row["status"], scores=row["scores"])
                        for row in response.json()["results"]]
                if [row.h3_index for row in rows] != [tile.h3_index for tile in part]:
                    raise ModelServiceError("Model results must match requested H3 cells in count and order")
                return rows

            # map preserves submission order even when batches finish in a different order.
            with ThreadPoolExecutor(max_workers=min(len(parts), MAX_CONCURRENCY)) as pool:
                return [row for batch in pool.map(call, parts) for row in batch]
    except (GoogleAuthError, OSError) as exc:
        raise ModelServiceError("Unable to obtain a Model Service ID token") from exc
    except httpx.TimeoutException as exc:
        raise ModelTimeoutError("Model Service prediction timed out") from exc
    except httpx.HTTPError as exc:
        raise ModelServiceError("Model Service prediction request failed") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise ModelServiceError("Invalid Model Service prediction response") from exc
