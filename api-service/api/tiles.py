"""Stable geographic identities come from the model bundle; legacy is explicit."""
from api.dimensions import PROVIDER
from api.schemas import Tile

FAKE_REGIONS = {'demo': (100, 100)}


def get_tiles(region_id: str) -> list[Tile]:
    if PROVIDER == 'bundle':
        from api.model_bundle import get_bundle
        bundle = get_bundle()
        if region_id != bundle.manifest['region_id']:
            raise ValueError(f'unknown region_id: {region_id}')
        return bundle.tiles
    if region_id not in FAKE_REGIONS:
        raise ValueError(f'unknown region_id: {region_id}')
    return fake_grid_tiles(region_id, *FAKE_REGIONS[region_id])


def fake_grid_tiles(region_id: str, rows: int, cols: int) -> list[Tile]:
    return [Tile(tile_id=f'{region_id}_r{r:03d}_c{c:03d}', row=r, col=c) for r in range(rows) for c in range(cols)]
