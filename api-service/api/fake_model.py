# Read the precomputed input and turn it into 20 score maps of 100×100
# input(rows, cols) → load the model result data → {dimension name: [[1*100][2*100][3*100]..[100*100]]}
# Delete this after the real model is connected

from pathlib import Path
import numpy as np

# Read the precomputed fake scores: 20 dimensions, each one a 100×100 map with 10000 numbers
SCORES_PATH = Path(__file__).resolve().parent.parent / "data" / "fake" / "charles_river_scores.npz"

# Read once and cache it; later requests use it directly without reading the file again
_cache: dict[str, np.ndarray] | None = None


# The public function: return one rows×cols (100×100) score map (0-1) for each dimension
def fake_score_maps(rows: int, cols: int) -> dict[str, np.ndarray]:
    global _cache
    if _cache is None:
        with np.load(SCORES_PATH) as data:
            _cache = {name: data[name] for name in data.files}

    # Grid size is the same as the precomputed one: use it directly
    stored_rows, stored_cols = next(iter(_cache.values())).shape
    if (rows, cols) == (stored_rows, stored_cols):
        return _cache

    # Not the same: resize to the requested size with nearest neighbor
    ri = np.arange(rows) * stored_rows // rows
    ci = np.arange(cols) * stored_cols // cols
    return {name: grid[np.ix_(ri, ci)] for name, grid in _cache.items()}
