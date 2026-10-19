# Automatically checks whether the scoring math in score_math.py is wrong. It makes a few small examples, e.g. 4 tiles with water scores 0, 0.2, 0.8, 0.4, then asserts what the result should be, e.g. "the tile with the most water gets the highest total score".
# Unit tests for score_math.py: pure math, no Gemini calls, no cost.
import pytest
from pydantic import ValidationError

from api import score_math
from api.dimensions import DIMENSION_NAMES
from api.schemas import TileScore



# Make a small region of 1 row and N cols: give the scores for a few dimensions; all other dimensions get 0.5 (score range 0-1)
def make_tiles(**dims: list[float]) -> list[TileScore]:
    n = len(next(iter(dims.values())))
    return [
        TileScore(
            tile_id=f"t{i}",
            row=0,
            col=i,
            scores={name: dims[name][i] if name in dims else 0.5 for name in DIMENSION_NAMES},
        )
        for i in range(n)
    ]


# Take out the single row of the heatmap
def first_row(heatmap):
    return heatmap[0]


# All weights 0 → every tile's total score is 0 (no preference, no highs or lows)
def test_zero_weights_give_flat_map():
    heatmap, _ = score_math.combine(make_tiles(shade=[0, 0.2, 0.8, 0.4]), weights={})
    assert first_row(heatmap) == [0, 0, 0, 0]


# Want water → the higher the water score, the higher the tile's total score
def test_wanted_dimension_ranks_highest():
    row = first_row(score_math.combine(make_tiles(shade=[0, 0.2, 0.8, 0.4]), {"shade": 1})[0])
    assert row.index(max(row)) == 2
    assert row[2] > row[3] > row[1] > row[0]


# Don't want cars → the ranking flips; the tile with the most cars gets the lowest total score
def test_negative_weight_flips_ranking():
    row = first_row(score_math.combine(make_tiles(traffic_intensity=[0, 0.2, 0.8, 0.4]), {"traffic_intensity": -1})[0])
    assert row.index(min(row)) == 2


# Shifting the whole mean doesn't change the result: z-score only looks at "how much above average"
def test_mean_shift_does_not_change_result():
    a = first_row(score_math.combine(make_tiles(shade=[0, 0.2, 0.8, 0.4]), {"shade": 1})[0])
    b = first_row(score_math.combine(make_tiles(shade=[0.1, 0.3, 0.9, 0.5]), {"shade": 1})[0])
    assert a == pytest.approx(b)


# σ floor: almost identical scores (difference < model error) won't be blown up into big differences
def test_sigma_floor_stops_noise_amplification():
    tiles = make_tiles(shade=[0.5, 0.505, 0.495, 0.5])
    with_floor = first_row(score_math.combine(tiles, {"shade": 1})[0])
    without_floor = first_row(score_math.combine(tiles, {"shade": 1}, rmse={"shade": 0.0})[0])
    assert max(abs(v) for v in with_floor) < 0.2
    assert max(abs(v) for v in without_floor) > 1.0


# Clip: the Z of one extreme tile goes up to ±3 at most
def test_z_is_clipped():
    row = first_row(score_math.combine(make_tiles(shade=[0] * 99 + [1.0]), {"shade": 1})[0])
    assert max(row) == pytest.approx(score_math.Z_CLIP)


# must_have: tiles whose raw score is below the gate are out (None) and get counted
def test_must_have_excludes_tiles():
    heatmap, stats = score_math.combine(
        make_tiles(shade=[0, 0.05, 0.15, 0.3]), {"shade": 1}, must_have=["shade"]
    )
    assert first_row(heatmap)[:2] == [None, None]
    assert None not in first_row(heatmap)[2:]
    assert stats.n_excluded == 2


# Heatmap shape: put back in the right place by row/col
def test_heatmap_follows_row_col():
    tiles = [
        TileScore(tile_id=f"r{r}c{c}", row=r, col=c,
                  scores={name: (1.0 if (r, c) == (1, 2) and name == "shade" else 0.0)
                          for name in DIMENSION_NAMES})
        for r in range(2) for c in range(3)
    ]
    heatmap, _ = score_math.combine(tiles, {"shade": 1})
    assert len(heatmap) == 2 and len(heatmap[0]) == 3
    assert heatmap[1][2] == max(v for row in heatmap for v in row)


# Range check: scores from the model outside 0-1 (e.g. 1.5 or 80, still given on 0-100) get stopped
@pytest.mark.parametrize("bad", [1.5, 80.0, -0.1])
def test_scores_outside_range_rejected(bad):
    with pytest.raises(ValidationError):
        make_tiles(shade=[bad])


def test_score_values_matches_combine():
    tiles = make_tiles(shade=[0, 0.05, 0.15, 0.3], quietness=[0.9, 0.1, 0.5, 0.4])
    weights, must = {"shade": 1, "quietness": -0.5}, ["shade"]
    heatmap, stats_a = score_math.combine(tiles, weights, must)
    values, stats_b = score_math.score_values(tiles, weights, must)
    assert values == first_row(heatmap)
    assert stats_a == stats_b
