"""Region standardization and preference scoring, independent of display geometry."""
import math
import numpy as np
from api.dimensions import DIMENSION_NAMES, SCORE_MIN, SCORE_MAX
from api.schemas import ScoreStats, TileScore

PLACEHOLDER_RMSE = 0.05 * (SCORE_MAX - SCORE_MIN)
Z_CLIP = 3.0
MUST_HAVE_MIN = SCORE_MIN + 0.1 * (SCORE_MAX - SCORE_MIN)


def to_matrix(tile_scores):
    return np.array([[t.scores[name] for name in DIMENSION_NAMES] for t in tile_scores], dtype=float)


def standardize(X, rmse):
    if not np.isfinite(X).all() or not np.isfinite(rmse).all() or (rmse < 0).any():
        raise ValueError('scores/errors must be finite; errors must be nonnegative')
    mu = X.mean(axis=0)
    sigma = np.maximum(np.maximum(X.std(axis=0), rmse), 1e-12)
    return np.clip((X - mu) / sigma, -Z_CLIP, Z_CLIP), mu, sigma


def score_values(tile_scores, weights, must_have=(), rmse=None):
    if not tile_scores:
        raise ValueError('cannot score an empty region')
    if set(weights) - set(DIMENSION_NAMES) or set(must_have) - set(DIMENSION_NAMES):
        raise ValueError('unsupported scoring dimension')
    if any(not math.isfinite(v) or not -1 <= v <= 1 for v in weights.values()):
        raise ValueError('weights must be finite and in [-1,1]')
    X = to_matrix(tile_scores)
    rmse_vec = np.array([(rmse or {}).get(name, PLACEHOLDER_RMSE) for name in DIMENSION_NAMES])
    Z, mu, sigma = standardize(X, rmse_vec)
    t = Z @ np.array([weights.get(name, 0.0) for name in DIMENSION_NAMES])
    passed = np.ones(len(t), dtype=bool)
    for name in must_have:
        passed &= X[:, DIMENSION_NAMES.index(name)] >= MUST_HAVE_MIN
    values = [round(float(value), 3) if ok else None for value, ok in zip(t, passed)]
    stats = ScoreStats(n_tiles=len(t), n_excluded=int((~passed).sum()),
        t_min=round(float(t[passed].min()), 3) if passed.any() else None,
        t_max=round(float(t[passed].max()), 3) if passed.any() else None,
        mean=dict(zip(DIMENSION_NAMES, np.round(mu, 4).tolist())),
        sigma=dict(zip(DIMENSION_NAMES, np.round(sigma, 4).tolist())))
    return values, stats


def combine(tile_scores: list[TileScore], weights: dict[str, float], must_have=(), rmse=None):
    """Legacy rectangular renderer; geographic rendering uses score_values directly."""
    values, stats = score_values(tile_scores, weights, must_have, rmse)
    if any(t.row is None or t.col is None for t in tile_scores):
        raise ValueError('grid combine requires explicit row/col positions')
    heatmap = [[None] * (max(t.col for t in tile_scores) + 1) for _ in range(max(t.row for t in tile_scores) + 1)]
    for tile, value in zip(tile_scores, values):
        heatmap[tile.row][tile.col] = value
    return heatmap, stats
