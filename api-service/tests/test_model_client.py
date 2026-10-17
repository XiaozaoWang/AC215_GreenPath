# Unit tests for model_client.check_tile_ids (the gatekeeper): the tiles the model sends back must match the ones we sent, one to one.
import pytest

from api import model_client
from api.dimensions import DIMENSION_NAMES
from api.schemas import Tile, TileScore


# Make 3 tiles: one row, three columns
TILES = [Tile(tile_id=f"t{c}", row=0, col=c) for c in range(3)]


# Make one model result (the scores are just made up, all 0.5)
def score(tile_id, row=0, col=0):
    return TileScore(tile_id=tile_id, row=row, col=col, scores={name: 0.5 for name in DIMENSION_NAMES})


# One to one: no error
def test_matching_tiles_pass():
    model_client.check_tile_ids(TILES, [score(t.tile_id, t.row, t.col) for t in TILES])


# Too few, too many, duplicates, wrong position: all must raise an error
@pytest.mark.parametrize("returned", [
    [score("t0", 0, 0), score("t1", 0, 1)],                                       # t2 is missing
    [score("t0", 0, 0), score("t1", 0, 1), score("t2", 0, 2), score("t9", 0, 9)],  # extra t9
    [score("t0", 0, 0), score("t0", 0, 0), score("t1", 0, 1), score("t2", 0, 2)],  # t0 sent twice
    [score("t0", 0, 0), score("t1", 0, 2), score("t2", 0, 1)],                    # t1 and t2 swapped places
])
def test_mismatch_rejected(returned):
    with pytest.raises(model_client.TileMismatchError):
        model_client.check_tile_ids(TILES, returned)


# Run the current fake model through predict(): the gatekeeper lets it pass
def test_predict_passes_check():
    assert len(model_client.predict(TILES)) == 3
