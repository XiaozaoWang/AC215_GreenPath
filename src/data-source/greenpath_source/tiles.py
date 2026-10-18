"""Standalone tiles stage extracted from en_02_tiles.ipynb; no notebook runtime required."""

if __name__ == "__main__":
    from greenpath_source.runtime import context
    globals().update(context("tiles", SOURCE_OPTIONS))
    # Extracted from en_02_tiles.ipynb, cell 7.
    import numpy as np
    cells_path = path("processed", "cells.parquet")
    cells = read_parquet(cells_path)
    if cells.empty:
        raise ValueError("No cells to crop; run streetview first")
    TILE_COLS = ["naip_item", "naip_year", "tile_path", "nodata_frac", "gsd", "error", "tile_ok", "tile_px", "ground_m"]
    cells = cells[[c for c in cells.columns if c not in TILE_COLS]].reset_index(drop=True)   # on a rerun, drop the old aerial-tile columns
    makedirs(path("processed", "tiles"))
    print(f"Read {len(cells)} cells (from 01)")

    # Extracted from en_02_tiles.ipynb, cell 9.
    import pystac_client, planetary_computer
    from shapely.geometry import shape, Point, box

    pad = 0.005
    BBOX = (cells.lon.min() - pad, cells.lat.min() - pad, cells.lon.max() + pad, cells.lat.max() + pad)

    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )
    search = catalog.search(collections=["naip"], bbox=BBOX,
                            datetime=f"{NAIP_MIN_YEAR}-01-01/2100-01-01")
    items = list(search.items())
    item_geoms = [(shape(it.geometry), it) for it in items]

    def item_year(it):
        return int(it.properties.get("naip:year", it.datetime.year))

    print(f"Found {len(items)} NAIP images, years:", sorted({item_year(it) for it in items}))

    # Extracted from en_02_tiles.ipynb, cell 10.
    DEG_PER_M_LAT = 1 / 111_320

    def pick_item(lon, lat):
        # first look for an image that fully covers the whole tile; if none, settle for one that covers the center point
        half_lat = GROUND_M / 2 * DEG_PER_M_LAT
        half_lon = half_lat / np.cos(np.radians(lat))
        footprint = box(lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat)
        full = [it for g, it in item_geoms if g.contains(footprint)]
        cands = full or [it for g, it in item_geoms if g.contains(Point(lon, lat))]
        if not cands:
            return None
        return max(cands, key=lambda it: (item_year(it), it.datetime))

    picked = [pick_item(r.lon, r.lat) for r in cells.itertuples()]
    cells["naip_item"] = [it.id if it else None for it in picked]
    cells["naip_year"] = pd.array([item_year(it) if it else None for it in picked], dtype="Int64")
    items_by_id = {it.id: it for it in items}

    print("Cells without NAIP coverage:", cells.naip_item.isna().sum())

    # Extracted from en_02_tiles.ipynb, cell 12.
    import rasterio
    from rasterio.windows import Window
    from rasterio.enums import Resampling
    from pyproj import Transformer
    from PIL import Image

    def crop_tile(src, lon, lat, ground_m=GROUND_M, out_px=TILE_PX):
        # return a (H, W, 3) uint8 array and the share of blank pixels
        to_src = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        x, y = to_src.transform(lon, lat)
        row, col = src.index(x, y)
        win = ground_m / abs(src.res[0])                      # side length of the square (in source pixels)
        window = Window(col - win / 2, row - win / 2, win, win)
        arr = src.read([1, 2, 3], window=window, out_shape=(3, out_px, out_px),
                       resampling=Resampling.bilinear, boundless=True, fill_value=0)
        img = np.moveaxis(arr, 0, -1).astype(np.uint8)
        nodata_frac = float((img.sum(axis=-1) == 0).mean())
        return img, nodata_frac

    def save_png(img, out_path):
        buf = io.BytesIO()
        Image.fromarray(img).save(buf, format="PNG")
        with fs.open(path(out_path), "wb") as f:
            f.write(buf.getvalue())

    # Extracted from en_02_tiles.ipynb, cell 13.
    from tqdm.auto import tqdm

    GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="3",
                    GDAL_HTTP_RETRY_DELAY="2", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif")

    results = {}   # cell_id -> dict(tile_path, nodata_frac, gsd, error)
    todo = cells.dropna(subset=["naip_item"])

    with rasterio.Env(**GDAL_ENV):
        for item_id, group in tqdm(todo.groupby("naip_item"), desc="NAIP images"):
            out_paths = {r.cell_id: f"processed/tiles/{r.cell_id}.png" for r in group.itertuples()}   # relative to the data root
            pending = []
            for r in group.itertuples():
                sidecar = path(out_paths[r.cell_id] + ".json")
                identity = {"naip_item": item_id, "lon": r.lon, "lat": r.lat, "ground_m": GROUND_M, "tile_px": TILE_PX}
                previous = json.load(open(sidecar)) if fs.exists(sidecar) else None
                if previous and fs.exists(path(out_paths[r.cell_id])) and previous["identity"] == identity and previous["image_sha256"] == file_digest(path(out_paths[r.cell_id])):
                    results[r.cell_id] = previous["result"]
                else:
                    pending.append(r)
            if not pending:
                continue
            # signed links expire; sign again before opening so long runs do not run into expiry
            href = planetary_computer.sign(items_by_id[item_id].assets["image"].href)
            try:
                with rasterio.open(href) as src:
                    gsd = abs(src.res[0])
                    for r in pending:
                        try:
                            img, nd = crop_tile(src, r.lon, r.lat)
                            save_png(img, out_paths[r.cell_id])
                            results[r.cell_id] = {"tile_path": out_paths[r.cell_id], "nodata_frac": nd, "gsd": gsd, "error": None}
                            safe_write_json(path(out_paths[r.cell_id] + ".json"), {
                                "identity": {"naip_item": item_id, "lon": r.lon, "lat": r.lat, "ground_m": GROUND_M, "tile_px": TILE_PX},
                                "image_sha256": file_digest(path(out_paths[r.cell_id])), "result": results[r.cell_id]})
                        except Exception as e:
                            results[r.cell_id] = {"tile_path": None, "nodata_frac": None, "gsd": gsd, "error": repr(e)}
            except Exception as e:
                for r in pending:
                    results[r.cell_id] = {"tile_path": None, "nodata_frac": None, "gsd": None, "error": repr(e)}

    res = pd.DataFrame.from_dict(results, orient="index").rename_axis("cell_id").reset_index()
    if res.empty:
        res = pd.DataFrame(columns=["cell_id", "tile_path", "nodata_frac", "gsd", "error"])
    print("succeeded:", res.tile_path.notna().sum(), "| failed:", res.error.notna().sum())
    if res.error.notna().any():
        print(res[res.error.notna()].head().to_string(index=False))

    # Extracted from en_02_tiles.ipynb, cell 15.
    def nodata_of(p):
        with fs.open(path(p), "rb") as f:
            img = np.array(Image.open(f).convert("RGB"))
        return float((img.sum(axis=-1) == 0).mean())

    need = res.tile_path.notna() & res.nodata_frac.isna()
    res.loc[need, "nodata_frac"] = [nodata_of(p) for p in res.loc[need, "tile_path"]]

    out = cells.merge(res, on="cell_id", how="left")
    out["tile_ok"] = out.tile_path.notna() & (out.nodata_frac.fillna(1) <= MAX_NODATA_FRAC)
    out["tile_px"] = TILE_PX
    out["ground_m"] = GROUND_M
    out["updated_at"] = now_iso()

    if not out.tile_ok.any():
        raise ValueError("No usable aerial tiles; retry tiles after checking coverage/network")
    write_parquet(out, cells_path)
    print(f"cells.parquet has {len(out)} rows, {int(out.tile_ok.sum())} usable aerial tiles")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    # Extracted headless report from en_02_tiles.ipynb, cell 17.
    import matplotlib.pyplot as plt

    show = out[out.tile_ok].sample(n=min(16, int(out.tile_ok.sum())), random_state=SEED)
    n = len(show); ncol = 4; nrow = max(int(np.ceil(n / ncol)), 1)
    fig, axes = plt.subplots(nrow, ncol, figsize=(3 * ncol, 3 * nrow))
    for ax in np.atleast_1d(axes).ravel():
        ax.axis("off")
    for ax, r in zip(np.atleast_1d(axes).ravel(), show.itertuples()):
        with fs.open(path(r.tile_path), "rb") as f:
            ax.imshow(Image.open(f))
        ax.set_title(f"{r.cell_id[-5:]} · {int(r.naip_year)}", fontsize=9)
    plt.tight_layout(); plt.savefig(path("processed", "tiles_preview.png")); plt.close(fig)

    bad = out[~out.tile_ok]
    print(f"{len(bad)} unusable")
    if len(bad):
        print(bad[["cell_id", "naip_item", "nodata_frac", "error"]].head(10).to_string(index=False))
    # Extracted headless report from en_02_tiles.ipynb, cell 19.
    gap = out.naip_year.astype("float") - pd.to_datetime(out.capture_max).dt.year
    print("NAIP year − latest street-photo year (positive = aerial tile is newer):")
    print(gap.value_counts().sort_index().to_string())
    write_parquet(gap.value_counts().sort_index().rename_axis("year_gap").reset_index(name="n_cells"), path("processed", "year_gap_counts.parquet"))

    # Extracted from en_02_tiles.ipynb, cell 21.
    manifest = write_manifest({
        "step": "02_tiles", "city": str(cells.city.iloc[0]) if len(cells) else None,
        "tile_px": TILE_PX, "ground_m": GROUND_M, "naip_min_year": NAIP_MIN_YEAR,
        "input_cells": int(len(cells)), "tiles_ok": int(out.tile_ok.sum()),
        "naip_years": {str(int(k)): int(v) for k, v in out.naip_year.value_counts().items()},
        "naip_item_ids": sorted(items_by_id),
        "outputs": ["processed/tiles", "processed/cells.parquet"],
        "finished_at": now_iso(),
    }, "02_tiles_manifest.json")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
