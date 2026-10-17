# Through fake_model.fake_score_maps we can read our model data into 20 tables of 100*100
# Through tiles.get_tiles we make list[Tile] from the current region name: an empty map table with 10000 items
# Here the predict function fills the scores into the tile list, and checks them with the check_tile_ids function called inside it
# M2: the model isn't ready yet, so for now we use one satellite image to fake the scores in advance

from api import fake_model
from api.dimensions import DIMENSION_NAMES
from api.schemas import Tile, TileScore


# Raise this error when the results the model sends back don't match the tiles we sent (the contract with the model team is broken)
class TileMismatchError(ValueError):
    pass


# Fill the real scores read by fake_model.fake_score_maps into the empty tile list made by tiles.get_tiles
# Each input item is a Tile, holding the tile's id and coordinates; each output item is a TileScore, which adds one more field to Tile: the scores for the 20 dimensions
def predict(tiles: list[Tile]) -> list[TileScore]:
    rows = max(t.row for t in tiles) + 1
    cols = max(t.col for t in tiles) + 1
    # FAKE: get the score maps from the fake model for now; once the real model is connected, only change this line
    maps = fake_model.fake_score_maps(rows, cols)
    tile_scores = [
        TileScore(
            tile_id=t.tile_id,
            row=t.row,
            col=t.col,
            # Score range 0-1, keep 3 decimals
            scores={name: round(float(maps[name][t.row, t.col]), 3) for name in DIMENSION_NAMES},
        )
        for t in tiles
    ]
    # Before handing it on, check first: not one tile more, not one less, and the positions are right
    check_tile_ids(tiles, tile_scores)
    return tile_scores


# This is called inside the function above and acts as a gatekeeper: the tiles we send and the tile_scores we get back must match one to one
# It guards against the real model (HTTP) sending back too few, too many, duplicates, or putting scores in the wrong place. If we don't report this kind of error, the heatmap still looks "pretty normal"
def check_tile_ids(tiles: list[Tile], tile_scores: list[TileScore]) -> None:
    sent = {t.tile_id: (t.row, t.col) for t in tiles}
    got_ids = [ts.tile_id for ts in tile_scores]

    # The same tile came back twice
    duplicates = sorted({i for i in got_ids if got_ids.count(i) > 1}) if len(got_ids) != len(set(got_ids)) else []
    # Missing ones and extra ones
    missing = sorted(set(sent) - set(got_ids))
    extra = sorted(set(got_ids) - set(sent))
    # The id matches but the row/col is wrong (the score would be drawn in the wrong place on the map)
    misplaced = sorted(ts.tile_id for ts in tile_scores if ts.tile_id in sent and sent[ts.tile_id] != (ts.row, ts.col))

    if duplicates or missing or extra or misplaced:
        # Show at most 5 examples per type, so we don't print all 10,000 tiles
        raise TileMismatchError(
            f"model output does not match the tiles sent: "
            f"missing={missing[:5]} ({len(missing)}), extra={extra[:5]} ({len(extra)}), "
            f"duplicated={duplicates[:5]} ({len(duplicates)}), misplaced={misplaced[:5]} ({len(misplaced)})"
        )
