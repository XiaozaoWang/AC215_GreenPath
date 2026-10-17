#Give a region id, get back the list of all tiles in that region.
#“demo” → list[Tile]

from api.schemas import Tile

# Fake region table, only one item
FAKE_REGIONS = {
    "demo": (100, 100),
}


# Public entry: one region id (eg. boston) → one table; 10000 items, each with id/r/c; raise an error for an unknown id
def get_tiles(region_id: str) -> list[Tile]: #input a region id, get a list where each item is “Tile(tile_id="demo_r000_c000", row=0, col=0)”, defined in schemas
    if region_id not in FAKE_REGIONS:#if the input id isn't in FAKE_REGIONS, raise an error
        raise ValueError(f"unknown region_id {region_id!r}, available: {list(FAKE_REGIONS)}")
    rows, cols = FAKE_REGIONS[region_id]#"demo" above is the id; take out the two values (100, 100)
    return fake_grid_tiles(region_id, rows, cols) 


# Follows the function above: make the map list List[Tile], where each item is “Tile(tile_id="demo_r000_c000", row=0, col=0)”
# This is split out on its own so that later, when we switch to the real way of cutting tiles (cut evenly by meters into ~150m tiles, each with coordinates), we only change this one function
def fake_grid_tiles(region_id: str, rows: int, cols: int) -> list[Tile]:
    return [
        Tile(tile_id=f"{region_id}_r{r:03d}_c{c:03d}", row=r, col=c)
        for r in range(rows)
        for c in range(cols)
    ]
