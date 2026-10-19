#Give a region id, get back the list of all tiles in that region.
#“demo” → list[Tile]

from api.schemas import Tile


def get_tiles(region_id: str, version_id: str | None = None) -> list[Tile]:
    from api.model_bundle import get_bundle
    bundle = get_bundle(version_id)
    if region_id != bundle.manifest["region_id"]:
        raise ValueError(f"unknown region_id {region_id!r}, available: {[bundle.manifest['region_id']]}")
    return bundle.tiles
