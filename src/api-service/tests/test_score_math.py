# Checks that the scoring math in score_math.py is right. It builds a few small examples, e.g. 4 cells with water scores 0, 0.2, 0.8, 0.4, then asserts what the result should be, e.g. "the cell with the most water has the highest total".
# Unit tests for score_math.py: pure math, no Gemini calls, no cost.
import pytest
from pydantic import ValidationError

from api import score_math
from api.dimensions import DIMENSION_NAMES
from api.schemas import TileScore


# Build a small area of N cells: set scores for a few dimensions, all other dimensions get 0.5 (scores range 0-1)
def make_tiles(**dims: list[float]) -> list[TileScore]:
    n = len(next(iter(dims.values())))
    return [
        TileScore(h3_index=f"t{i}", scores={name: dims[name][i] if name in dims else 0.5 for name in DIMENSION_NAMES})
        for i in range(n)
    ]


# combine returns the list of total scores per cell (same order as the input)
def first_row(values):
    return values


# All weights 0, so every cell's total is 0 (no preference means no high or low)
def test_zero_weights_give_flat_map():
    heatmap, _ = score_math.combine(make_tiles(water_access=[0, 0.2, 0.8, 0.4]), weights={})
    assert first_row(heatmap) == [0, 0, 0, 0]


# Want water, so the higher a cell's water score, the higher its total
def test_wanted_dimension_ranks_highest():
    row = first_row(score_math.combine(make_tiles(water_access=[0, 0.2, 0.8, 0.4]), {"water_access": 1})[0])
    assert row.index(max(row)) == 2
    assert row[2] > row[3] > row[1] > row[0]


# Don't want wide roads, so the ranking flips and the cell with the widest road gets the lowest total
def test_negative_weight_flips_ranking():
    row = first_row(score_math.combine(make_tiles(road_width=[0, 0.2, 0.8, 0.4]), {"road_width": -1})[0])
    assert row.index(min(row)) == 2


# Shifting the whole mean doesn't change the result: z-score only looks at "how far above average"
def test_mean_shift_does_not_change_result():
    a = first_row(score_math.combine(make_tiles(water_access=[0, 0.2, 0.8, 0.4]), {"water_access": 1})[0])
    b = first_row(score_math.combine(make_tiles(water_access=[0.1, 0.3, 0.9, 0.5]), {"water_access": 1})[0])
    assert a == pytest.approx(b)


# Sigma floor: nearly equal scores (difference < model error) don't get blown up into big differences
def test_sigma_floor_stops_noise_amplification():
    tiles = make_tiles(water_access=[0.5, 0.505, 0.495, 0.5])
    with_floor = first_row(score_math.combine(tiles, {"water_access": 1})[0])
    without_floor = first_row(score_math.combine(tiles, {"water_access": 1}, rmse={"water_access": 0.0})[0])
    assert max(abs(v) for v in with_floor) < 0.2
    assert max(abs(v) for v in without_floor) > 1.0


# Clipping: the Z of one extreme cell goes up to +/-3 at most
def test_z_is_clipped():
    row = first_row(score_math.combine(make_tiles(water_access=[0] * 99 + [1.0]), {"water_access": 1})[0])
    assert max(row) == pytest.approx(score_math.Z_CLIP)


# must_have: cells whose raw score is below the threshold are out (None) and get counted
def test_must_have_excludes_tiles():
    heatmap, stats = score_math.combine(
        make_tiles(water_access=[0, 0.05, 0.15, 0.3]), {"water_access": 1}, must_have=["water_access"]
    )
    assert first_row(heatmap)[:2] == [None, None]
    assert None not in first_row(heatmap)[2:]
    assert stats.n_excluded == 2


# Result order: the total for the n-th cell is at the n-th position
def test_values_follow_input_order():
    values, _ = score_math.combine(make_tiles(water_access=[0, 0, 1.0, 0, 0]), {"water_access": 1})
    assert len(values) == 5
    assert values.index(max(values)) == 2


# Range check: scores from the model outside 0-1 (e.g. 1.5 or 80 from a model still on a 0-100 scale) get rejected
@pytest.mark.parametrize("bad", [1.5, 80.0, -0.1])
def test_scores_outside_range_rejected(bad):
    with pytest.raises(ValidationError):
        make_tiles(water_access=[bad])
