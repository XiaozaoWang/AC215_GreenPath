"""Standalone streetview stage extracted from en_01_streetview.ipynb; no notebook runtime required."""

if __name__ == "__main__":
    from greenpath_source.runtime import context
    globals().update(context("streetview", SOURCE_OPTIONS))
    # Extracted from en_01_streetview.ipynb, cell 7.
    for d in ["raw/grid", "raw/mapillary/images", f"raw/mapillary/metadata/{CITY}", "processed", "runs"]:
        makedirs(path(d))

    # Extracted from en_01_streetview.ipynb, cell 9.
    import time, requests

    TOKEN = os.environ.get("MAPILLARY_TOKEN")
    if not TOKEN:
        raise ValueError("Set MAPILLARY_TOKEN in the container environment")

    API = "https://graph.mapillary.com"

    class MapillaryError(RuntimeError):
        def __init__(self, status, body, auth=False):
            super().__init__(f"Mapillary returned HTTP {status}; check credentials, quota and availability")
            self.status, self.auth = status, auth

    def mapillary_get(url, params=None, tries=4):
        last = None
        for k in range(tries):
            r = requests.get(url, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            last = MapillaryError(r.status_code, r.text,
                                  auth=(r.status_code in (400, 401, 403) or "OAuth" in r.text or '"code":190' in r.text))
            if last.auth:                                    # bad token: retrying is pointless, fail immediately
                raise MapillaryError(r.status_code, r.text + "\n→ the token is invalid or was not read; check MAPILLARY_TOKEN in .env", auth=True)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** k)
                continue
            raise last
        raise last

    def download_bytes(url, tries=4):
        for k in range(tries):
            try:
                r = requests.get(url, timeout=60)
                if r.status_code == 200:
                    return r.content
            except requests.RequestException:
                pass
            time.sleep(2 ** k)
        return None

    # Extracted from en_01_streetview.ipynb, cell 11.
    import numpy as np, mercantile, mapbox_vector_tile
    from tqdm.auto import tqdm

    TILE_URL = "https://tiles.mapillary.com/maps/vtp/mly1_public/2/{z}/{x}/{y}"
    META_COLS = ["image_id", "lon", "lat", "captured_at_ms", "compass_raw", "is_pano", "sequence", "quality"]
    R_EARTH = 6378137.0

    def fetch_vector_tile(t, tries=4):
        last = None
        for k in range(tries):
            r = requests.get(TILE_URL.format(z=t.z, x=t.x, y=t.y), params={"access_token": TOKEN}, timeout=180)
            if r.status_code == 200:
                return r.content
            if r.status_code in (204, 404):                  # this tile has no street photos
                return b""
            last = MapillaryError(r.status_code, r.text,
                                  auth=(r.status_code in (400, 401, 403) or "OAuth" in r.text))
            if last.auth:
                raise MapillaryError(r.status_code, r.text + "\n→ the token is invalid or was not read; check MAPILLARY_TOKEN in .env", auth=True)
            time.sleep(2 ** k)
        raise last

    def decode_tile(content, t):
        # decode the "image" layer of the tile into a table; convert tile coordinates (0–4096) to longitude/latitude
        if not content:
            return pd.DataFrame(columns=META_COLS)
        layer = mapbox_vector_tile.decode(content, default_options={"y_coord_down": True}).get("image")
        feats = [f for f in (layer["features"] if layer else []) if f["geometry"]["type"] == "Point"]
        if not feats:
            return pd.DataFrame(columns=META_COLS)
        ext = layer.get("extent", 4096)
        xy = np.array([f["geometry"]["coordinates"] for f in feats], dtype=float)
        b = mercantile.xy_bounds(t)
        mx = b.left + xy[:, 0] / ext * (b.right - b.left)
        my = b.top - xy[:, 1] / ext * (b.top - b.bottom)
        pr = [f["properties"] for f in feats]
        return pd.DataFrame({
            "image_id": [str(p["id"]) for p in pr],
            "lon": np.degrees(mx / R_EARTH),
            "lat": np.degrees(2 * np.arctan(np.exp(my / R_EARTH)) - np.pi / 2),
            "captured_at_ms": [p.get("captured_at") for p in pr],
            "compass_raw": [p.get("compass_angle") for p in pr],
            "is_pano": [bool(p.get("is_pano")) for p in pr],
            "sequence": [p.get("sequence_id") for p in pr],
            "quality": [p.get("quality_score") for p in pr],
        })

    tiles = list(mercantile.tiles(*BBOX, zooms=[TILE_ZOOM]))
    parts = []
    for t in tqdm(tiles, desc="Downloading street-photo tiles"):
        p = path("raw", "mapillary", "metadata", CITY, f"tile_z{t.z}_{t.x}_{t.y}.parquet")
        if fs.exists(p):                                  # already downloaded, skip
            parts.append(read_parquet(p)); continue
        df_t = decode_tile(fetch_vector_tile(t), t).drop_duplicates("image_id")
        write_parquet(df_t, p)
        parts.append(df_t)

    raw = pd.concat(parts, ignore_index=True).drop_duplicates("image_id")
    raw["is_pano"] = raw.is_pano.fillna(False).astype(bool)      # an empty tile makes the column object-typed; unify the types here
    for c in ["lon", "lat", "captured_at_ms", "compass_raw", "quality"]:
        raw[c] = pd.to_numeric(raw[c])
    print(f"{len(tiles)} tiles, metadata of {len(raw):,} street photos")

    # Extracted from en_01_streetview.ipynb, cell 13.
    import h3

    df = raw.copy()
    df["captured_at"] = pd.to_datetime(df.captured_at_ms, unit="ms", utc=True)
    df["compass"] = df.compass_raw
    min_lon, min_lat, max_lon, max_lat = BBOX

    funnel = [("all", len(df))]
    def step(name, mask):
        global df
        df = df[mask(df)]
        funnel.append((name, len(df)))

    step("inside BBOX", lambda d: d.lon.between(min_lon, max_lon) & d.lat.between(min_lat, max_lat))
    step(f"taken in {MIN_YEAR} or later", lambda d: d.captured_at.dt.year >= MIN_YEAR)
    step(f"excluding months {EXCLUDE_MONTHS}", lambda d: ~d.captured_at.dt.month.isin(EXCLUDE_MONTHS))
    if not ALLOW_PANO:
        step("not panoramas", lambda d: ~d.is_pano)
    if MIN_QUALITY > 0:
        step(f"quality score ≥ {MIN_QUALITY}", lambda d: d.quality.fillna(0) >= MIN_QUALITY)
    step("has heading", lambda d: d.compass.notna() | d.is_pano)

    df["compass"] = df.compass.fillna(0.0) % 360
    df["h3_index"] = [h3.latlng_to_cell(la, lo, H3_RES) for la, lo in zip(df.lat, df.lon)]

    for name, n in funnel:
        print(f"{name:<24}{n:>9,}")

    # Extracted from en_01_streetview.ipynb, cell 15.
    def pick_diverse(g, k):
        g = g.sort_values("captured_at", ascending=False).copy()
        g["quadrant"] = (g.compass // 90).astype(int) % 4
        chosen = g.groupby("quadrant").head(1).sort_values("captured_at", ascending=False).head(k)
        if len(chosen) < k:
            chosen = pd.concat([chosen, g.drop(chosen.index).head(k - len(chosen))])
        return chosen

    if df.empty:
        raise ValueError("No street images remain after filtering; check the area and filters")
    counts_raw = df.groupby("h3_index").size().rename("n_images_filtered")
    selected = pd.concat([pick_diverse(g, MAX_IMAGES_PER_CELL) for _, g in df.groupby("h3_index")])
    per_cell = (selected.groupby("h3_index")
                .agg(n_images_selected=("image_id", "size"),
                     n_quadrants=("quadrant", "nunique"),
                     capture_min=("captured_at", "min"), capture_max=("captured_at", "max"))
                .join(counts_raw))
    print(f"{len(df)} usable street photos → in {len(per_cell)} cells → {len(selected)} selected")

    # Extracted from en_01_streetview.ipynb, cell 17.

    def build_grid(bbox, res, city, prefix):
        lo0, la0, lo1, la1 = bbox
        poly = h3.LatLngPoly([(la0, lo0), (la0, lo1), (la1, lo1), (la1, lo0)])
        rows = []
        for c in sorted(h3.polygon_to_cells(poly, res)):
            lat, lon = h3.cell_to_latlng(c)
            rows.append({"cell_id": f"{prefix}_{c}", "city": city, "h3_index": c,
                         "h3_res": res, "lat": lat, "lon": lon})
        return pd.DataFrame(rows)

    grid = build_grid(BBOX, H3_RES, CITY, CITY_PREFIX)
    write_parquet(grid, path("raw", "grid", f"{CITY}_h3r{H3_RES}.parquet"))

    grid = grid.join(per_cell, on="h3_index")
    grid["n_images_selected"] = grid.n_images_selected.fillna(0).astype(int)
    n_any = int((grid.n_images_selected >= 1).sum())
    n_ok = int((grid.n_images_selected >= MIN_IMAGES_PER_CELL).sum())
    print(f"Grid has {len(grid)} cells")
    print(f"  ≥1 usable street photo: {n_any} ({n_any / len(grid):.1%})")
    print(f"  ≥{MIN_IMAGES_PER_CELL} usable street photos (eligible): {n_ok} ({n_ok / len(grid):.1%})")

    # Extracted from en_01_streetview.ipynb, cell 19.
    eligible = grid[grid.n_images_selected >= MIN_IMAGES_PER_CELL]
    if SAMPLE_SIZE and SAMPLE_SIZE < len(eligible):
        cells = eligible.sample(n=SAMPLE_SIZE, random_state=SEED)
    else:
        cells = eligible
    if cells.empty:
        raise ValueError("No eligible cells satisfy the minimum street-image count")
    cells = cells.sort_values("cell_id").reset_index(drop=True)
    cells["capture_min"] = cells.capture_min.dt.strftime("%Y-%m")
    cells["capture_max"] = cells.capture_max.dt.strftime("%Y-%m")

    sv = selected[selected.h3_index.isin(cells.h3_index)].copy()
    sv["cell_id"] = f"{CITY_PREFIX}_" + sv.h3_index
    sv["captured_at"] = sv.captured_at.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    sv["image_path"] = [f"raw/mapillary/images/{i}.jpg" for i in sv.image_id]   # path relative to the data root
    sv = sv[["image_id", "cell_id", "h3_index", "lat", "lon", "captured_at", "compass", "quadrant",
             "is_pano", "sequence", "image_path"]].sort_values(["cell_id", "image_id"]).reset_index(drop=True)
    print(f"Sampled {len(cells)} cells, {len(sv)} street photos in total")

    # Extracted from en_01_streetview.ipynb, cell 21.
    def fetch_image(image_id, out_path):
        js = mapillary_get(f"{API}/{image_id}", {"access_token": TOKEN, "fields": "thumb_1024_url"})
        url = js.get("thumb_1024_url")
        data = download_bytes(url) if url else None
        if data is None:
            return False
        with fs.open(path(out_path), "wb") as f:
            f.write(data)
        return True

    def download_one(r):
        # return whether this photo is already on disk; failures are only recorded and never stop the whole batch
        try:
            from PIL import Image
            if fs.exists(path(r.image_path)):
                try:
                    with fs.open(path(r.image_path), "rb") as stream:
                        Image.open(stream).verify()
                    return True
                except (OSError, ValueError):
                    pass
            if not fetch_image(r.image_id, r.image_path):
                return False
            with fs.open(path(r.image_path), "rb") as stream:
                Image.open(stream).verify()
            return True
        except Exception as e:
            print(f"  photo {r.image_id} failed to download: {type(e).__name__}")
            return False

    sv["downloaded"] = False
    if DOWNLOAD_IMAGES:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
            sv["downloaded"] = list(tqdm(ex.map(download_one, list(sv.itertuples())), total=len(sv), desc="Downloading street photos"))
    print(f"Downloaded {int(sv.downloaded.sum())} / {len(sv)}")

    # Extracted from en_01_streetview.ipynb, cell 23.
    if DOWNLOAD_IMAGES:
        ok = sv[sv.downloaded]
        n_ok_img = ok.groupby("cell_id").size()
        keep = n_ok_img[n_ok_img >= MIN_IMAGES_PER_CELL].index
        sv = sv[sv.cell_id.isin(keep) & sv.downloaded].reset_index(drop=True)
        cells = cells[cells.cell_id.isin(keep)].reset_index(drop=True)
    if cells.empty:
        raise ValueError("No cells have enough successfully downloaded images; rerun to resume downloads")
    cells["n_images"] = cells.cell_id.map(sv.groupby("cell_id").size()).astype(int)
    cells["updated_at"] = now_iso()

    for record in sv.itertuples():
        with fs.open(path(record.image_path), "rb") as stream:
            from PIL import Image
            Image.open(stream).verify()
    write_parquet(sv, path("processed", "streetview.parquet"))
    write_parquet(cells, path("processed", "cells.parquet"))
    print(f"cells.parquet: {len(cells)} cells; streetview.parquet: {len(sv)} photos")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    # Extracted headless report from en_01_streetview.ipynb, cell 17.
    fig, ax = plt.subplots(figsize=(8, 6))
    for lo, hi, col, lab in [(0, 0, "lightgray", "no street view"),
                             (1, MIN_IMAGES_PER_CELL - 1, "orange", f"fewer than {MIN_IMAGES_PER_CELL}"),
                             (MIN_IMAGES_PER_CELL, 99, "seagreen", "eligible")]:
        s = grid[grid.n_images_selected.between(lo, hi)]
        ax.scatter(s.lon, s.lat, s=4, c=col, label=f"{lab} ({len(s)})")
    ax.set_aspect(1 / np.cos(np.radians((min_lat + max_lat) / 2)))
    ax.set_xlabel("lon"); ax.set_ylabel("lat"); ax.legend(markerscale=3)
    ax.set_title(f"{CITY}: Mapillary coverage per H3 cell (res {H3_RES})")
    plt.savefig(path("processed", "coverage.png")); plt.close(fig)
    # Extracted headless report from en_01_streetview.ipynb, cell 25.
    from PIL import Image

    if DOWNLOAD_IMAGES and len(cells):
        show = cells.sample(n=min(4, len(cells)), random_state=SEED).cell_id
        fig, axes = plt.subplots(len(show), MAX_IMAGES_PER_CELL, squeeze=False, figsize=(3.2 * MAX_IMAGES_PER_CELL, 2.6 * len(show)))
        axes = np.atleast_2d(axes)
        for ax in axes.ravel():
            ax.axis("off")
        for r_i, cid in enumerate(show):
            for c_i, r in enumerate(sv[sv.cell_id == cid].head(MAX_IMAGES_PER_CELL).itertuples()):
                with fs.open(path(r.image_path), "rb") as f:
                    axes[r_i, c_i].imshow(Image.open(f))
                axes[r_i, c_i].set_title(f"{cid[-5:]} · {r.compass:.0f}° · {r.captured_at[:7]}", fontsize=8)
        plt.tight_layout(); plt.savefig(path("processed", "streetview_preview.png")); plt.close(fig)
    # Extracted from en_01_streetview.ipynb, cell 27.
    manifest = write_manifest({
        "step": "01_streetview", "city": CITY, "bbox": BBOX, "h3_res": H3_RES,
        "min_year": MIN_YEAR, "exclude_months": list(EXCLUDE_MONTHS), "allow_pano": ALLOW_PANO,
        "min_images_per_cell": MIN_IMAGES_PER_CELL, "max_images_per_cell": MAX_IMAGES_PER_CELL,
        "sample_size": SAMPLE_SIZE, "seed": SEED,
        "filter_funnel": dict(funnel),
        "grid_cells": int(len(grid)), "cells_with_any_image": n_any, "cells_eligible": n_ok,
        "coverage_eligible_frac": round(n_ok / len(grid), 4),
        "cells_sampled": int(len(cells)), "images_selected": int(len(sv)),
        "images_downloaded": int(sv.downloaded.sum()),
        "attribution": "Street-level imagery © Mapillary contributors, CC-BY-SA 4.0",
        "outputs": ["raw/grid", "raw/mapillary", "processed/streetview.parquet", "processed/cells.parquet"],
        "finished_at": now_iso(),
    }, "01_streetview_manifest.json")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
