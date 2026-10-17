# Scoring math. Goal: model raw scores X + preference weights w → total score t per tile.
# Steps: z-score within the region (σ has a floor) → clip → t = Z·w → must_have gate → put back on the grid.
import numpy as np

from api.dimensions import DIMENSION_NAMES
from api.schemas import ScoreStats, TileScore

# σ floor: a difference smaller than the model error doesn't count as a difference.
# PLACEHOLDER: M2 doesn't have the real model's per-dimension RMSE yet, so for now assume the model is off by 0.05 on average (score range 0-1);
# later read it from the model's metrics.json
PLACEHOLDER_RMSE = 0.05
# z-score clip range: keep a few extreme tiles from taking over
Z_CLIP = 3.0
# must_have gate: tiles whose raw score on this dimension is below this value are out right away (0-1 range, same as 10 points before)
MUST_HAVE_MIN = 0.1


# Take the list from model_client.predict, where each item holds all the info of one tile,
# and turn it into a 10000 rows * 20 cols matrix, dropping the id info
def to_matrix(tile_scores: list[TileScore]) -> np.ndarray:
    return np.array([[ts.scores[name] for name in DIMENSION_NAMES] for ts in tile_scores], dtype=float)


# Standardize within the region: for each dimension subtract the region mean, divide by σ (σ is at least RMSE), then clip to ±Z_CLIP
def standardize(X: np.ndarray, rmse: np.ndarray):
    mu = X.mean(axis=0)
    sigma = np.maximum(X.std(axis=0), rmse)
    Z = np.clip((X - mu) / sigma, -Z_CLIP, Z_CLIP)
    return Z, mu, sigma


# Main function: raw scores + weights → heatmap (heatmap[row][col], tiles that didn't pass the gate are None) + intermediate numbers
def combine(
    tile_scores: list[TileScore],
    weights: dict[str, float],
    must_have: list[str] = (),
    rmse: dict[str, float] | None = None,
) -> tuple[list[list[float | None]], ScoreStats]:
    # Standardize: turn each dimension into "how many σ above/below the region average"
    X = to_matrix(tile_scores)
    rmse_vec = np.array([(rmse or {}).get(name, PLACEHOLDER_RMSE) for name in DIMENSION_NAMES])
    Z, mu, sigma = standardize(X, rmse_vec)

    # Weighted sum: t = Z·w, one total score per tile
    w = np.array([weights.get(name, 0.0) for name in DIMENSION_NAMES])
    t = Z @ w

    # must_have gate: use the raw scores to guard the gate (not Z, because Z only says relatively high or low)
    passed = np.ones(len(t), dtype=bool)
    for name in must_have:
        passed &= X[:, DIMENSION_NAMES.index(name)] >= MUST_HAVE_MIN

    # Put back on the grid
    rows = max(ts.row for ts in tile_scores) + 1
    cols = max(ts.col for ts in tile_scores) + 1
    heatmap = [[None] * cols for _ in range(rows)]
    for ts, value, ok in zip(tile_scores, t, passed):
        heatmap[ts.row][ts.col] = round(float(value), 3) if ok else None

    # Intermediate numbers: for the page to show and for people to check
    stats = ScoreStats(
        n_tiles=len(t),
        n_excluded=int((~passed).sum()),
        t_min=round(float(t[passed].min()), 3) if passed.any() else None,
        t_max=round(float(t[passed].max()), 3) if passed.any() else None,
        # Keep 4 decimals for the 0-1 range (it was 2 decimals back with 0-100)
        mean={name: round(float(m), 4) for name, m in zip(DIMENSION_NAMES, mu)},
        sigma={name: round(float(s), 4) for name, s in zip(DIMENSION_NAMES, sigma)},
    )
    return heatmap, stats
