"""Model adapter boundary. A missing real artifact never falls back to fake scores."""
from collections import Counter
from api.dimensions import DIMENSION_NAMES, PROVIDER
from api.schemas import Tile, TileScore


class TileMismatchError(ValueError):
    pass


def predict(tiles: list[Tile], version_id: str | None = None) -> list[TileScore]:
    if not tiles:
        raise TileMismatchError('cannot predict an empty region')
    if PROVIDER == 'bundle':
        from api.model_bundle import get_bundle
        scores = get_bundle(version_id).scores
        try:
            result = [scores[t.tile_id] for t in tiles]
        except KeyError as error:
            raise TileMismatchError(f'prediction missing for {error.args[0]}') from error
    else:
        from api.fake_model import fake_score_maps
        rows, cols = max(t.row for t in tiles) + 1, max(t.col for t in tiles) + 1
        maps = fake_score_maps(rows, cols)
        result = [TileScore(**t.model_dump(), scores={name: round(float(maps[name][t.row, t.col]), 3) for name in DIMENSION_NAMES}) for t in tiles]
    check_tile_ids(tiles, result)
    return result


def check_tile_ids(tiles: list[Tile], tile_scores: list[TileScore]) -> None:
    sent = {t.tile_id: (t.row, t.col, t.h3_index, t.geometry) for t in tiles}
    got = Counter(t.tile_id for t in tile_scores)
    if len(sent) != len(tiles) or set(sent) != set(got) or any(n != 1 for n in got.values()):
        raise TileMismatchError('model output has missing, extra or duplicate tile IDs')
    if any(sent[t.tile_id] != (t.row, t.col, t.h3_index, t.geometry) for t in tile_scores):
        raise TileMismatchError('model output changed a tile position or geometry')
