"""Standalone features stage extracted from en_03_osm_features.ipynb; no notebook runtime required."""

if __name__ == "__main__":
    from greenpath_source.runtime import context
    globals().update(context("features", SOURCE_OPTIONS))
    # Extracted from en_03_osm_features.ipynb, cell 7.
    import numpy as np
    cells = read_parquet(path("processed", "cells.parquet"))
    if "tile_ok" in cells:
        cells = cells[cells.tile_ok.fillna(False)]           # only process cells that already have an aerial tile
    cells = cells.reset_index(drop=True)
    if cells.empty:
        raise ValueError("No usable cells; run streetview and tiles first")
    if "ground_m" in cells and cells.ground_m.notna().any():
        GROUND_M = float(cells.ground_m.dropna().iloc[0])
    print(f"{len(cells)} cells, each summarizing a {GROUND_M:.0f} m × {GROUND_M:.0f} m square")

    # Extracted from en_03_osm_features.ipynb, cell 9.
    import geopandas as gpd

    pts = gpd.GeoSeries(gpd.points_from_xy(cells.lon, cells.lat), crs=4326)
    UTM = pts.estimate_utm_crs()
    foot = gpd.GeoDataFrame({"h3_index": cells.h3_index},
                            geometry=pts.to_crs(UTM).buffer(GROUND_M / 2, cap_style="square"), crs=UTM)
    FOOT_AREA = GROUND_M ** 2
    west, south, east, north = pts.total_bounds
    OSM_BBOX = (west - OSM_PAD_DEG, south - OSM_PAD_DEG, east + OSM_PAD_DEG, north + OSM_PAD_DEG)   # left, bottom, right, top
    print("UTM zone:", UTM.to_string()[:40], "| OSM read extent:", [round(v, 4) for v in OSM_BBOX])

    # Extracted from en_03_osm_features.ipynb, cell 11.
    import requests, time, warnings
    from tqdm.auto import tqdm


    PBF_NAME = OSM_PBF_URL.rsplit("/", 1)[-1]
    META_REL = f"raw/osm/{PBF_NAME}.meta.json"
    # pyrosm needs a local file; in local / Drive mode it goes into the data directory, in GCS mode into a local cache directory
    PBF = path("raw", "osm", PBF_NAME) if STORAGE != "gcs" else os.path.join("cache", PBF_NAME)
    os.makedirs(os.path.dirname(PBF) or ".", exist_ok=True)

    if not os.path.exists(PBF):
        print(f"Downloading {OSM_PBF_URL}")
        with requests.get(OSM_PBF_URL, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            meta = {"url": OSM_PBF_URL, "last_modified": r.headers.get("last-modified"),
                    "downloaded_at": now_iso(), "size_bytes": total}
            with open(PBF + ".part", "wb") as f, tqdm(total=total, unit="B", unit_scale=True, desc=PBF_NAME) as pb:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk); pb.update(len(chunk))
        os.replace(PBF + ".part", PBF)
        with fs.open(path(META_REL), "w") as f:
            json.dump(meta, f, indent=2)

    try:
        with fs.open(path(META_REL), "r") as f:
            OSM_META = json.load(f)
    except Exception:                                       # the file was placed here by hand and has no download record
        OSM_META = {"url": OSM_PBF_URL, "size_bytes": os.path.getsize(PBF),
                    "note": "placed manually, no download record", "file_mtime": datetime.datetime.fromtimestamp(os.path.getmtime(PBF)).isoformat()}
    OSM_META["sha256"] = file_digest(PBF)
    print("OSM snapshot:", OSM_META.get("last_modified") or OSM_META.get("file_mtime"), f" ({os.path.getsize(PBF) / 1e6:.0f} MB)")

    # Extracted from en_03_osm_features.ipynb, cell 12.
    import pyrosm

    osm = pyrosm.OSM(PBF, bounding_box=list(OSM_BBOX))        # [left, bottom, right, top]; read only this extent

    bar = tqdm(total=5, desc="Reading OSM", bar_format="{l_bar}{bar} {n_fmt}/{total_fmt} [{elapsed}] {postfix}")
    def step(label, fn):
        # show on the progress bar what is being done before each step, and print the elapsed time afterwards
        bar.set_postfix_str(f"reading: {label}")
        t0 = time.time()
        out = fn()
        bar.update(1)
        tqdm.write(f"  ✓ {label} ({time.time() - t0:.0f} s)")
        return out

    def layer(tags):
        g = osm.get_data_by_custom_criteria(custom_filter=tags, filter_type="keep",
                                            keep_nodes=False, keep_ways=True, keep_relations=True)
        if g is None or len(g) == 0:
            return gpd.GeoDataFrame(geometry=[], crs=4326).to_crs(UTM)
        return g[["geometry"]].reset_index(drop=True).to_crs(UTM)

    def load_roads():
        return osm.get_network(network_type="all", nodes=True,
                               extra_attributes=["lanes", "maxspeed", "cycleway", "cycleway:left", "cycleway:right", "cycleway:both"])

    def load_pois():
        p = osm.get_pois(custom_filter={"amenity": True, "shop": True, "tourism": True})
        if p is None or len(p) == 0:
            return gpd.GeoDataFrame(geometry=[], crs=4326).to_crs(UTM)
        p = p[["geometry"]].to_crs(UTM).reset_index(drop=True)
        p["geometry"] = p.geometry.representative_point()      # for area POIs (e.g. a mall) take a representative point so each is counted only once
        return p

    nodes, edges = step("road network", load_roads)
    if nodes is None or edges is None or edges.empty:
        raise ValueError("The OSM extent has no road network; check the snapshot and area")
    edges = edges.to_crs(UTM).reset_index(drop=True)
    degree = pd.concat([edges.u, edges.v]).value_counts()      # how many road segments each node connects; ≥3 is an intersection
    nodes = nodes.to_crs(UTM)
    nodes["street_count"] = nodes.id.map(degree).fillna(0).astype(int)
    water = step("water", lambda: layer({"natural": ["water", "wetland"], "waterway": ["riverbank", "dock"], "landuse": ["reservoir", "basin"]}))
    green = step("green space", lambda: layer({"leisure": ["park", "garden", "playground", "recreation_ground", "nature_reserve", "golf_course"],
                                        "landuse": ["grass", "meadow", "forest", "village_green", "recreation_ground", "cemetery"],
                                        "natural": ["wood", "scrub", "grassland"]}))
    buildings = step("buildings", lambda: layer({"building": True}))
    pois = step("POI", load_pois)
    bar.set_postfix_str("done"); bar.close()
    print(f"Roads: {len(edges)} segments, {int((nodes.street_count >= 3).sum())} intersection nodes")
    print(f"water {len(water)} | green {len(green)} | buildings {len(buildings)} | POI {len(pois)}")

    # Extracted from en_03_osm_features.ipynb, cell 14.
    CLASSES = {
        "major": {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link"},
        "secondary": {"secondary", "secondary_link", "tertiary", "tertiary_link"},
        "local": {"residential", "unclassified", "living_street", "service"},
        "path": {"cycleway", "footway", "path", "pedestrian", "track", "steps", "bridleway"},
    }

    def first(v):                                   # a few tags can be lists; take the first element
        return v[0] if isinstance(v, (list, tuple)) else v

    def road_class(h):
        h = first(h)
        return next((k for k, s in CLASSES.items() if h in s), "other")

    def has_bike(row):
        if first(row.get("highway")) == "cycleway":
            return True
        for c in ("cycleway", "cycleway:left", "cycleway:right", "cycleway:both"):
            v = first(row.get(c))
            if isinstance(v, str) and v not in ("no", "none", "separate"):
                return True
        return False

    def to_num(v):
        v = first(v)
        try:
            return float(str(v).split()[0])
        except (ValueError, TypeError, IndexError):
            return np.nan

    edges["rclass"] = edges.highway.map(road_class)
    edges["bike"] = edges.apply(has_bike, axis=1)
    edges["lanes_n"] = edges["lanes"].map(to_num) if "lanes" in edges else np.nan
    edges["speed"] = edges["maxspeed"].map(to_num) if "maxspeed" in edges else np.nan
    inter_nodes = nodes[nodes.street_count >= 3]

    def area_frac(layer, fp):
        if layer.empty:
            return 0.0
        cand = layer.iloc[layer.sindex.query(fp, predicate="intersects")]
        if cand.empty:
            return 0.0
        return float(min(cand.geometry.intersection(fp).union_all().area / fp.area, 1.0))

    def wmean(values, weights):
        ok = values.notna()
        return float(np.average(values[ok], weights=weights[ok])) if ok.any() and weights[ok].sum() > 0 else np.nan

    rows = []
    for h3_index, fp in zip(foot.h3_index, foot.geometry):
        e = edges.iloc[edges.sindex.query(fp, predicate="intersects")].copy()
        e["len_in"] = e.geometry.intersection(fp).length
        row = {"h3_index": h3_index}
        for k in list(CLASSES) + ["other"]:
            row[f"road_{k}_m"] = float(e.loc[e.rclass == k, "len_in"].sum())
        row["road_total_m"] = float(e.len_in.sum())
        row["bike_lane_m"] = float(e.loc[e.bike, "len_in"].sum())
        drive = e[e.rclass.isin(["major", "secondary", "local"])]
        row["lanes_avg"] = wmean(drive.lanes_n, drive.len_in)
        row["maxspeed_avg"] = wmean(drive.speed, drive.len_in)
        row["intersections"] = int(len(inter_nodes.iloc[inter_nodes.sindex.query(fp, predicate="intersects")]))
        row["poi_count"] = int(len(pois.iloc[pois.sindex.query(fp, predicate="intersects")])) if len(pois) else 0
        row["osm_water_frac"] = area_frac(water, fp)
        row["osm_green_frac"] = area_frac(green, fp)
        row["osm_building_frac"] = area_frac(buildings, fp)
        rows.append(row)

    osm_feat = pd.DataFrame(rows)

    # Extracted from en_03_osm_features.ipynb, cell 16.
    import planetary_computer, pystac_client, rasterio
    from rasterio.windows import Window
    from shapely.geometry import shape, Point
    from tqdm.auto import tqdm

    WC = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built", 60: "bare",
          70: "snow", 80: "water", 90: "wetland", 95: "mangrove", 100: "moss"}

    catalog = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1",
                                        modifier=planetary_computer.sign_inplace)
    wc_items = list(catalog.search(collections=["esa-worldcover"], bbox=OSM_BBOX,
                                   datetime=f"{WORLDCOVER_YEAR}-01-01/{WORLDCOVER_YEAR}-12-31").items())
    print(f"Found {len(wc_items)} WorldCover images")
    wc_geoms = [(shape(it.geometry), it) for it in wc_items]
    foot_ll = foot.to_crs(4326)

    wc_rows, covered = [], set()
    for it_geom, it in sorted(wc_geoms, key=lambda pair: pair[1].id):
        idx = [i for i, c in enumerate(cells.itertuples()) if i not in covered and it_geom.covers(Point(c.lon, c.lat))]
        if not idx:
            continue
        href = planetary_computer.sign(it.assets["map"].href)
        with rasterio.open(href) as src:
            for i in tqdm(idx, desc=it.id[-12:]):
                minx, miny, maxx, maxy = foot_ll.geometry.iloc[i].bounds
                r0, c0 = src.index(minx, maxy); r1, c1 = src.index(maxx, miny)
                arr = src.read(1, window=Window(c0, r0, max(c1 - c0 + 1, 1), max(r1 - r0 + 1, 1)))
                counts = np.bincount(arr.ravel(), minlength=101)
                tot = max(counts.sum(), 1)
                row = {"h3_index": cells.h3_index.iloc[i]}
                for code_, name in WC.items():
                    row[f"wc_{name}_frac"] = float(counts[code_] / tot)
                row["wc_dominant"] = WC.get(int(counts.argmax()) if arr.size else 0, "unknown")
                wc_rows.append(row)
                covered.add(i)

    if not wc_rows or len(covered) != len(cells):
        raise ValueError("WorldCover does not cover every selected cell; check source coverage before publishing")
    wc_feat = pd.DataFrame(wc_rows)
    print(f"WorldCover covers {len(wc_feat)} / {len(cells)} cells")

    # Extracted from en_03_osm_features.ipynb, cell 18.
    feat = cells[["cell_id", "h3_index"]].merge(osm_feat, on="h3_index", how="left", validate="one_to_one").merge(wc_feat, on="h3_index", how="left", validate="one_to_one")
    feat["updated_at"] = now_iso()
    write_parquet(feat, path("processed", "osm_features.parquet"))
    print(f"osm_features.parquet: {len(feat)} rows, {feat.shape[1] - 3} feature columns")
    print("Columns with the most missing values:")
    print(feat.isna().sum().sort_values(ascending=False).head(5).to_string())

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    # Extracted headless report from en_03_osm_features.ipynb, cell 20.
    import matplotlib.pyplot as plt
    from PIL import Image

    show = feat.merge(cells[["h3_index", "tile_path"]], on="h3_index").sample(n=min(8, len(feat)), random_state=42)
    fig, axes = plt.subplots(2, 4, figsize=(13, 7.5))
    for ax in axes.ravel():
        ax.axis("off")
    for ax, r in zip(axes.ravel(), show.itertuples()):
        with fs.open(path(r.tile_path), "rb") as f:
            ax.imshow(Image.open(f))
        ax.set_title(f"major {r.road_major_m:.0f}m · bike {r.bike_lane_m:.0f}m\n"
                     f"green {r.osm_green_frac:.2f} · water {r.osm_water_frac:.2f} · tree {r.wc_tree_frac:.2f}", fontsize=8)
    plt.tight_layout(); plt.savefig(path("processed", "features_preview.png")); plt.close(fig)
    # Extracted from en_03_osm_features.ipynb, cell 22.
    manifest = write_manifest({
        "step": "03_osm_features", "input_cells": int(len(cells)), "output_rows": int(len(feat)),
        "ground_m": GROUND_M, "osm_bbox": [round(v, 5) for v in OSM_BBOX],
        "osm_road_segments": int(len(edges)), "osm_buildings": int(len(buildings)), "osm_pois": int(len(pois)),
        "worldcover_year": WORLDCOVER_YEAR, "worldcover_cells": int(len(wc_feat)),
        "worldcover_items": sorted(it.id for _, it in wc_geoms),
        "attribution": "© OpenStreetMap contributors (ODbL); ESA WorldCover 2021 v200 (CC BY 4.0)",
        "osm_snapshot": OSM_META, "pyrosm_version": pyrosm.__version__,
        "outputs": ["processed/osm_features.parquet"],
        "finished_at": now_iso(),
    }, "03_osm_features_manifest.json")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
