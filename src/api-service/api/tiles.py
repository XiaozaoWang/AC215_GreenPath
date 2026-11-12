"""Read region H3 cells from GCS using Application Default Credentials."""
import os
import re
from functools import lru_cache
from io import BytesIO

import h3
import pyarrow as pa
import pyarrow.parquet as pq
from google.api_core.exceptions import GoogleAPIError, NotFound
from google.auth.exceptions import GoogleAuthError
from google.cloud import storage

from api.schemas import Tile

DEFAULT_REGION = "city_ma_boston_big"


class UnknownRegionError(ValueError):
    pass


class RegionLoadError(RuntimeError):
    pass


@lru_cache(maxsize=32)
def _load_cells(project: str, bucket: str, dataset: str, region_id: str) -> tuple[str, ...]:
    # Cache only region membership. Predictions are always requested from Model Service.
    path = f"datasets/{dataset}/cells/{region_id}.parquet"
    try:
        data = storage.Client(project=project).bucket(bucket).blob(path).download_as_bytes(timeout=60)
    except NotFound as exc:
        raise UnknownRegionError(f"Unknown region: {region_id}") from exc
    except (GoogleAPIError, GoogleAuthError, OSError) as exc:
        raise RegionLoadError(f"Unable to load region {region_id} from GCS") from exc

    try:
        cells = pq.read_table(BytesIO(data), columns=["h3_index"])["h3_index"].to_pylist()
        if not cells or any(not isinstance(cell, str) or not h3.is_valid_cell(cell)
                            or h3.get_resolution(cell) != 10 for cell in cells):
            raise ValueError("Expected resolution-10 H3 strings")
        return tuple(dict.fromkeys(cells))  # Remove duplicates without changing file order.
    except (pa.ArrowException, KeyError, ValueError, TypeError) as exc:
        raise RegionLoadError(f"Invalid H3 Parquet data for region {region_id}") from exc


def get_tiles(region_id: str = DEFAULT_REGION) -> list[Tile]:
    # Region IDs select one file, so reject path separators before contacting GCS.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", region_id):
        raise UnknownRegionError(f"Invalid region: {region_id}")
    cells = _load_cells(os.getenv("GCP_PROJECT", "organic-victory-508017-v9"),
                        os.getenv("GREENPATH_BUCKET", "ac215-notebook"),
                        os.getenv("GREENPATH_DATASET", "1026_Full_data"), region_id)
    return [Tile(h3_index=cell) for cell in cells]
