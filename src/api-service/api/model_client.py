# Through fake_model.fake_score_maps we can read our model data into 20 tables of 100*100
# Through tiles.get_tiles we make list[Tile] from the current region name: an empty map table with 10000 items
# Here the predict function fills the scores into the tile list, and checks them with the check_tile_ids function called inside it
# M2: the model isn't ready yet, so for now we use one satellite image to fake the scores in advance

from api.schemas import Tile, TileScore


# Raise this error when the results the model sends back don't match the tiles we sent (the contract with the model team is broken)
class TileMismatchError(ValueError):
    pass


def predict(tiles: list[Tile], version_id: str | None = None) -> list[TileScore]:
    from api.model_bundle import get_bundle
    scores = get_bundle(version_id).scores
    missing = [t.tile_id for t in tiles if t.tile_id not in scores]
    if missing:
        raise TileMismatchError(f"prediction missing for {missing[:5]} ({len(missing)})")
    tile_scores = [scores[t.tile_id] for t in tiles]
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
