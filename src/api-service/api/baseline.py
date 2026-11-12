"""Area baseline: predicted scores of the cells of a region, used as the fixed yardstick for route matching.

Standardizing a cell against the whole area (instead of only the cells of one request) makes its match value depend
only on its own scores and the preference, so colours do not shift while predictions arrive batch by batch.
The file holds one row per cell: h3_index plus the 15 model scores (data/baselines/<region_id>.parquet).
"""
import os
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from api.dimensions import DIMENSION_NAMES

BASELINE_ROOT = Path(os.getenv("ROUTE_BASELINE_DIR", Path(__file__).resolve().parents[1] / "data" / "baselines"))
_lock = threading.Lock()


@lru_cache(maxsize=4)
def _read(region_id: str) -> np.ndarray | None:
    path = BASELINE_ROOT / f"{region_id}.parquet"
    if not path.is_file():
        return None
    table = pq.read_table(path).to_pydict()
    X = np.array([table[name] for name in DIMENSION_NAMES], dtype=float).T
    return X[~np.isnan(X).any(axis=1)]


def load(region_id: str) -> np.ndarray | None:
    """(cells, 15) raw scores in DIMENSION_NAMES order, or None when the region has no baseline file."""
    if "/" in region_id or "\\" in region_id or region_id.startswith("."):
        return None
    with _lock:
        return _read(region_id)
