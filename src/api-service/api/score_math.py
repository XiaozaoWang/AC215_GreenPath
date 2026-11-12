"""Standardize scenery scores, apply preference weights, and exclude unavailable cells."""
import numpy as np

from api.dimensions import DIMENSION_NAMES
from api.schemas import ScoreStats, TileScore

# Until per-dimension model RMSE is available, use 0.05 as the standard-deviation floor.
PLACEHOLDER_RMSE = 0.05
Z_CLIP = 3.0
MUST_HAVE_MIN = 0.1


def to_matrix(tile_scores: list[TileScore]) -> np.ndarray:
    return np.array([[ts.scores[name] for name in DIMENSION_NAMES] for ts in tile_scores], dtype=float)


def standardize(X: np.ndarray, rmse: np.ndarray):
    # The floor avoids amplifying tiny differences; clipping limits outliers.
    mu = X.mean(axis=0)
    sigma = np.maximum(X.std(axis=0), rmse)
    return np.clip((X - mu) / sigma, -Z_CLIP, Z_CLIP), mu, sigma


def combine(tile_scores: list[TileScore], weights: dict[str, float],
            must_have: list[str] = (), rmse: dict[str, float] | None = None
            ) -> tuple[list[float | None], ScoreStats]:
    # Calculate statistics only from cells with predictions, keeping their original positions.
    indices = [i for i, row in enumerate(tile_scores) if row.scores is not None]
    values = [None] * len(tile_scores)
    if not indices:
        return values, ScoreStats(n_tiles=len(values), n_excluded=len(values),
                                  t_min=None, t_max=None, mean={}, sigma={})
    X = to_matrix([tile_scores[i] for i in indices])
    rmse_vec = np.array([(rmse or {}).get(name, PLACEHOLDER_RMSE) for name in DIMENSION_NAMES])
    Z, mu, sigma = standardize(X, rmse_vec)
    t = Z @ np.array([weights.get(name, 0.0) for name in DIMENSION_NAMES])

    # Must-have thresholds use raw scores, rather than region-relative z-scores.
    passed = np.ones(len(t), dtype=bool)
    for name in must_have:
        passed &= X[:, DIMENSION_NAMES.index(name)] >= MUST_HAVE_MIN
    for index, value, ok in zip(indices, t, passed):
        if ok:
            values[index] = round(float(value), 3)

    return values, ScoreStats(
        n_tiles=len(values), n_excluded=len(values) - int(passed.sum()),
        t_min=round(float(t[passed].min()), 3) if passed.any() else None,
        t_max=round(float(t[passed].max()), 3) if passed.any() else None,
        mean={name: round(float(m), 4) for name, m in zip(DIMENSION_NAMES, mu)},
        sigma={name: round(float(s), 4) for name, s in zip(DIMENSION_NAMES, sigma)},
    )
