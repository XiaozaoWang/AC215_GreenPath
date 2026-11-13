"""
GreenPath data collector (was the 01 notebook): turns the bboxes in a config file into a dataset.
Per cell: one NAIP aerial tile + OSM / WorldCover map features; city bboxes also get Mapillary street photos. No scoring (that is in data-labeler).

Usage:
  python cli.py --config configs/1026_Full_data.yaml --areas city_ma_boston_big sub_ma_hadley_farm
  python cli.py --config configs/1026_Full_data.yaml --areas all --no-qc

Done work is skipped, so a rerun picks up where it stopped. Teammates share one config file and each list their own bboxes in --areas.
"""
import argparse, base64, datetime, html, io, json, os, re, shutil, subprocess, time, uuid, warnings
from concurrent.futures import ThreadPoolExecutor

import fsspec, gcsfs, h3, mapbox_vector_tile, mercantile, requests, yaml
import numpy as np, pandas as pd
import geopandas as gpd, pyrosm, planetary_computer, pystac_client, rasterio
import folium
import matplotlib
matplotlib.use("Agg")                                     # no screen, figures are only saved to files
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.colors import to_hex
from matplotlib.patches import Patch
from dotenv import load_dotenv, find_dotenv
from PIL import Image
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.windows import Window
from shapely.geometry import Point, Polygon, box, shape
from tqdm import tqdm

warnings.filterwarnings("ignore")

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
CONFIG_KEYS = ["h3_res", "min_year", "exclude_months", "min_images_per_cell", "max_images_per_cell",
               "tile_px", "ground_m", "naip_min_year", "worldcover_year"]
MAX_NODATA = 0.05                                         # an aerial tile is usable only if at most 5% of it is black empty pixels
CROP_WORKERS = 8                                          # how many aerial tiles to crop at the same time

fs = gcsfs.GCSFileSystem()
gs = lambda rel: f"gs://{BUCKET}/{rel}"                   # relative path in the bucket to full gs:// path
now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ==================== Status records ====================
# Each step of each bbox has only three results: complete (done) / incomplete (not done because of a temporary problem like network, rerun it) / not needed (does not apply, e.g. no street view for suburbs)
# "Data just isn't there" (nobody took photos, no NAIP coverage...) is not incomplete, it goes in each cell's tile_reason / street_reason
STEPS = {}                                                # {area_id: {step: {"status": ..., "reason": ..., other details}}}

class Incomplete(Exception):
    pass                                                  # used when the code itself decides a step is not done: Incomplete(reason, details)

def mark(area_id, step, status, reason="", **detail):
    STEPS.setdefault(area_id, {})[step] = {"status": status, **({"reason": reason} if reason else {}), **detail}
    if status == "incomplete":
        print(f"!!! {area_id}: {step} NOT complete: {reason}")

def step_status(area_id, step):
    return STEPS.get(area_id, {}).get(step, {}).get("status")

def why(e):
    # turn an error into a one-line reason: network / rate limit / server busy / no permission; otherwise give the raw error (most likely a code bug)
    if isinstance(e, Incomplete):
        return e.args[0]
    code = getattr(getattr(e, "response", None), "status_code", None)
    if code in (401, 403):
        return f"no permission (HTTP {code}): check MAPILLARY_TOKEN / Google login before rerunning"
    if code == 429:
        return "rate limited (HTTP 429)"
    if code and code >= 500:
        return f"server error (HTTP {code})"
    if isinstance(e, requests.Timeout):
        return "network timeout"
    if isinstance(e, requests.ConnectionError):
        return "network error (connection failed)"
    if type(e).__name__ == "RasterioIOError":
        return f"could not read the NAIP image (network?): {str(e)[:150]}"
    return f"error: {repr(e)[:200]}"

# Timing: at the end of each big step, print how many minutes this step and the whole run took
T0 = T_LAST = time.time()
TIMES = {}
def tick(step):
    global T_LAST
    TIMES[step] = (time.time() - T_LAST) / 60
    T_LAST = time.time()
    print(f"⏱ {step}: {TIMES[step]:.1f} min (total {(T_LAST - T0) / 60:.1f} min)")


# ==================== 1. Config + connect to bucket ====================
def mapillary_token():
    # local: MAPILLARY_TOKEN from .env (or docker --env-file); cloud: get mapillary-token from the group project's Secret Manager
    if os.environ.get("MAPILLARY_TOKEN"):
        return os.environ["MAPILLARY_TOKEN"]
    import google.auth
    from google.cloud import secretmanager
    project = google.auth.default()[1]
    name = f"projects/{project}/secrets/mapillary-token/versions/latest"
    return secretmanager.SecretManagerServiceClient().access_secret_version(name=name).payload.data.decode().strip()

def setup(args):
    global CONFIG, AREAS, RUN, BUCKET, DS, TOKEN, TILE_DIR, QC_DIR, catalog
    # read the config file (local path or gs:// both work); params become global constants H3_RES, GROUND_M..., same as in the notebook
    with fsspec.open(args.config) as f:
        cfg = yaml.safe_load(f)
    for k in CONFIG_KEYS:
        globals()[k.upper()] = cfg[k]
    AREAS = cfg["areas"]
    cfg["dataset_id"] = args.dataset or cfg["dataset_id"]   # dataset name: use --dataset if given (the workflow passes it), otherwise the one in the config file
    assert len({a["area_id"] for a in AREAS}) == len(AREAS), "two bboxes in the config share the same area_id"
    CONFIG = json.loads(json.dumps({k: v for k, v in cfg.items() if k != "dataset_id"}))   # same format as what is saved in manifest.json, so easy to compare

    # which bboxes to run this time: --areas all = all of them; a wrong name raises an error right away
    by_id = {a["area_id"]: a for a in AREAS}
    missing = [x for x in args.areas if x != "all" and x not in by_id]
    assert not missing, f"these area_ids are not in the config: {missing}"
    RUN = AREAS if args.areas == ["all"] else [by_id[x] for x in args.areas]

    BUCKET, DS = args.bucket, f"datasets/{cfg['dataset_id']}"
    TILE_DIR = f"cache/naip_tiles/{GROUND_M}m_{TILE_PX}px"   # shared by all datasets: the same NAIP image and the same cell are only cropped once
    QC_DIR = f"qc/{datetime.datetime.now():%Y%m%d-%H%M%S}"    # QC figures: keep a local copy, then upload to {DS}/qc/ in the bucket
    catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    TOKEN = mapillary_token() if any(a["area_type"] == "city" for a in RUN) else None

    # dataset already exists: params must be exactly the same; existing bboxes can't change, new ones can only be added at the end (so data from two configs doesn't mix in one dataset)
    rel = f"{DS}/manifest.json"
    if fs.exists(gs(rel)):
        manifest = json.load(fs.open(gs(rel)))
        saved = manifest["config"]
        same = {k: v for k, v in saved.items() if k != "areas"} == {k: v for k, v in CONFIG.items() if k != "areas"}
        changed = [a["area_id"] for a in saved["areas"] if by_id.get(a["area_id"]) != a]
        if not same or changed:
            raise SystemExit(f"Dataset {DS} already exists with a different config "
                             f"({'changed bboxes: ' + str(changed) if changed else 'parameters differ'}). Use a new dataset_id.")
        if len(AREAS) > len(saved["areas"]):              # new bboxes were added: record them in manifest
            manifest["config"] = CONFIG
            with fs.open(gs(rel), "w") as f:
                json.dump(manifest, f, indent=2)
        print(f"Dataset {DS} exists with the same config: reusing what is already there.")
    else:
        with fs.open(gs(rel), "w") as f:
            json.dump({"dataset_id": cfg["dataset_id"], "created_at": now(), "status": "running", "config": CONFIG}, f, indent=2)
        print(f"Created gs://{BUCKET}/{DS}/")


# ==================== 2. Cells (with dedup) ====================
def full_grid(bbox):
    # all H3 cells (about 130 m wide) whose centre is inside the bbox, with or without street view
    lo0, la0, lo1, la1 = bbox
    poly = h3.LatLngPoly([(la0, lo0), (la0, lo1), (la1, lo1), (la1, lo0)])
    ids = sorted(h3.polygon_to_cells(poly, H3_RES))
    return pd.DataFrame([(c, *h3.cell_to_latlng(c)) for c in ids], columns=["h3_index", "lat", "lon"])

def make_cells():
    # dedup: when two bboxes overlap, a cell belongs to only one bbox. City bboxes first (they have street view), then whichever comes first in the config
    # first compute the cells of all bboxes in the config (just math, a few seconds), so ownership works even when only some bboxes are run
    grids = {a["area_id"]: full_grid(a["bbox"]) for a in AREAS}
    owner = {}
    for a in sorted(AREAS, key=lambda a: a["area_type"] != "city"):       # stable sort: cities first, same type keeps config order
        for h in grids[a["area_id"]].h3_index:
            owner.setdefault(h, a["area_id"])
    parts = []
    for a in RUN:
        g = grids[a["area_id"]]
        mine = g.h3_index.map(owner) == a["area_id"]
        if (~mine).any():
            print(f"{a['area_id']}: {(~mine).sum()} cells also in another bbox, kept there")
        parts.append(g[mine].assign(area_id=a["area_id"], area_type=a["area_type"], area_subtype=a["area_subtype"]))
    cells = pd.concat(parts, ignore_index=True)
    cells.insert(0, "cell_id", cells.area_id + "_" + cells.h3_index)    # so you can see at a glance which bbox a cell belongs to
    print(cells.groupby(["area_id", "area_type", "area_subtype"]).size().rename("cells").to_string())
    return cells


# ==================== 3. Aerial tiles (NAIP) ====================
year_of = lambda it: int(it.properties.get("naip:year", it.datetime.year))
black = lambda img: img.sum(axis=-1) == 0                 # black empty pixels
black_frac = lambda img: float(black(img).mean())

def read_png(rel):
    with fs.open(gs(rel), "rb") as f:
        return np.asarray(Image.open(f).convert("RGB"))

def write_png(rel, img):
    # build the whole PNG in memory, then write it to the bucket, so no half-written images are left
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    with fs.open(gs(rel), "wb") as f:
        f.write(buf.getvalue())

def find_naip(cells):
    # for each cell find the newest NAIP from NAIP_MIN_YEAR on that covers the cell centre, record its id, year, capture date
    # only searches the catalog, no image download; each bbox is searched on its own (if bboxes are spread over several states, one combined search returns half the US)
    # returns: the NAIP images used {id: item}, and all NAIP near each bbox (needed for patching seams)
    cells["naip_item"], cells["naip_date"] = None, None
    cells["naip_year"] = pd.array([pd.NA] * len(cells), dtype="Int64")
    items, near = {}, {}
    for a in RUN:
        area = cells[cells.area_id == a["area_id"]]
        try:
            lo0, la0, lo1, la1 = a["bbox"]
            found = catalog.search(collections=["naip"], datetime=f"{NAIP_MIN_YEAR}-01-01/2100-01-01",
                                   bbox=(lo0 - 0.005, la0 - 0.005, lo1 + 0.005, la1 + 0.005)).items()
            naip = [(shape(it.geometry), it) for it in found]
            picked = [max((it for g, it in naip if g.contains(Point(r.lon, r.lat))), key=lambda it: (year_of(it), it.datetime), default=None)
                      for r in area.itertuples()]
            cells.loc[area.index, "naip_item"] = [it.id if it else None for it in picked]
            cells.loc[area.index, "naip_year"] = [year_of(it) if it else pd.NA for it in picked]
            cells.loc[area.index, "naip_date"] = [str(it.datetime.date()) if it else None for it in picked]
            items |= {it.id: it for it in picked if it}
            near[a["area_id"]] = [it for _, it in naip]
            print(f"{a['area_id']}: {len(naip)} NAIP images, years {sorted({year_of(it) for _, it in naip})}, "
                  f"{sum(it is None for it in picked)} cells without NAIP coverage")
        except Exception as e:
            mark(a["area_id"], "naip", "incomplete", f"NAIP search failed: {why(e)}")
    return items, near

def crop_from(item, jobs):
    # open a NAIP image once, crop a GROUND_M square around each cell centre, resize to TILE_PX x TILE_PX; if a read fails now and then, wait a few seconds and retry (up to 3 times)
    # each job is (id, lon, lat, save path): with a path, save as PNG and return the empty fraction; if path is None, return the image itself (for seam patching)
    out = {}
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"), \
         rasterio.open(planetary_computer.sign(item.assets["image"].href)) as src:
        to_src = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        win = GROUND_M / abs(src.res[0])                  # how many source pixels the tile width covers
        for key, lon, lat, rel in jobs:
            row, col = src.index(*to_src.transform(lon, lat))
            for k in range(3):
                try:
                    arr = src.read([1, 2, 3], window=Window(col - win / 2, row - win / 2, win, win), out_shape=(3, TILE_PX, TILE_PX),
                                   resampling=Resampling.bilinear, boundless=True, fill_value=0)
                    break
                except rasterio.errors.RasterioIOError:
                    if k == 2:
                        raise
                    time.sleep(5 * (k + 1))
            img = np.moveaxis(arr, 0, -1).astype(np.uint8)
            if rel:
                write_png(rel, img)
                out[key] = black_frac(img)
            else:
                out[key] = img
    return out

def patch_seams(cells, area, naip):
    # patch seams: when a cell sits on the border of two NAIP images, using only one image leaves half of it black
    # fill the black pixels from neighbouring NAIP (same year first, then newer is better) until black is <= 5%; patched tiles are saved to mosaic/{H3}.png
    # these cells are rare, so they are re-patched every run (a few minutes)
    bad = cells.loc[area.index][cells.loc[area.index].nodata_frac > MAX_NODATA]
    plan, jobs = {}, {}                                   # plan: {cell: NAIP images to use, in order}; jobs: {NAIP id: cells to crop from it}
    for i, r in bad.iterrows():
        sq = box(r.lon - 0.001, r.lat - 0.001, r.lon + 0.001, r.lat + 0.001)          # about 154 m square
        plan[i] = sorted([it for it in naip if it.id != r.naip_item and shape(it.geometry).intersects(sq)],
                         key=lambda it: (year_of(it) != r.naip_year, -year_of(it), -it.datetime.timestamp()))
        for it in plan[i]:
            jobs.setdefault(it.id, (it, []))[1].append((i, r.lon, r.lat, None))
    with ThreadPoolExecutor(8) as ex:
        crops = {(i, item_id): img for item_id, d in ex.map(lambda v: (v[0].id, crop_from(*v)), jobs.values()) for i, img in d.items()}
    n = 0
    for i, r in bad.iterrows():
        img, used = read_png(r.tile_path).copy(), [r.naip_item]
        for it in plan[i]:
            if black_frac(img) <= MAX_NODATA:
                break
            add = crops[(i, it.id)]
            fill = black(img) & ~black(add)
            if fill.any():
                img[fill] = add[fill]
                used.append(it.id)
        if black_frac(img) <= MAX_NODATA:
            rel = f"{TILE_DIR}/mosaic/{r.h3_index}.png"
            write_png(rel, img)
            cells.loc[i, ["tile_path", "nodata_frac", "naip_mosaic"]] = [rel, black_frac(img), ",".join(used)]
            n += 1
    return n

def crop_tiles(cells, items, near):
    # crop one aerial tile per cell into the shared cache; already cropped ones are not cropped again, only read back to check the empty fraction
    cells["tile_path"], cells["nodata_frac"], cells["naip_mosaic"] = None, np.nan, None
    for a in tqdm(RUN, desc="bboxes"):
        aid = a["area_id"]
        if step_status(aid, "naip") == "incomplete":
            continue                                      # the NAIP search above already failed
        try:
            area = cells[(cells.area_id == aid) & cells.naip_item.notna()]
            n_new = 0
            for item_id, group in area.groupby("naip_item"):
                have = set(fs.find(f"{BUCKET}/{TILE_DIR}/{item_id}"))    # already cropped by any dataset
                jobs = [(i, r.lon, r.lat, f"{TILE_DIR}/{item_id}/{r.h3_index}.png") for i, r in group.iterrows()]
                cached = [j for j in jobs if f"{BUCKET}/{j[3]}" in have]
                todo = [j for j in jobs if f"{BUCKET}/{j[3]}" not in have]
                with ThreadPoolExecutor(16) as ex:        # existing ones: read back 16 at a time, only to compute the empty fraction
                    for (i, _, _, rel), img in zip(cached, ex.map(read_png, [j[3] for j in cached])):
                        cells.loc[i, ["tile_path", "nodata_frac"]] = [rel, black_frac(img)]
                if todo:                                  # new ones to crop: CROP_WORKERS threads each open their own copy of the NAIP (one copy can't be shared by threads)
                    shares = [todo[k::CROP_WORKERS] for k in range(CROP_WORKERS) if todo[k::CROP_WORKERS]]
                    rel_of = {j[0]: j[3] for j in todo}
                    with ThreadPoolExecutor(len(shares)) as ex:
                        for part in ex.map(lambda s: crop_from(items[item_id], s), shares):
                            for i, nodata in part.items():
                                cells.loc[i, ["tile_path", "nodata_frac"]] = [rel_of[i], nodata]
                    n_new += len(todo)
            n_mosaic = patch_seams(cells, area, near[aid])
            mark(aid, "naip", "complete", tiles_new=n_new, tiles_mosaic=n_mosaic)
            print(f"{aid}: {len(area)} tiles ({n_new} newly cropped, {n_mosaic} seams patched)")
        except Exception as e:
            mark(aid, "naip", "incomplete", f"aerial tiles: {why(e)}")   # tiles already cropped stay in the cache, a rerun won't crop them again

    # write each cell's aerial tile state: ok / ok_mosaic (seam patched) / no_naip_coverage (no NAIP here) / mostly_black (mostly empty)
    cells["tile_ok"] = cells.tile_path.notna() & (cells.nodata_frac <= MAX_NODATA)
    cells["tile_reason"] = np.where(cells.tile_ok, np.where(cells.naip_mosaic.notna(), "ok_mosaic", "ok"),
                                    np.where(cells.naip_item.isna(), "no_naip_coverage", "mostly_black"))
    cells.loc[cells.area_id.map(lambda i: step_status(i, "naip") != "complete"), "tile_reason"] = None   # left empty for bboxes that are not done
    print(f"{int(cells.tile_ok.sum())} / {len(cells)} usable aerial tiles")


# ==================== 4. Street view (Mapillary, city bboxes only) ====================
R_EARTH = 6378137.0
M_LAT = 110574.0                                          # meters per degree of latitude
META_COLS = ["image_id", "lon", "lat", "captured_at_ms", "compass_raw", "is_pano", "sequence", "quality"]
SV_COLS = ["image_id", "cell_id", "h3_index", "area_id", "lat", "lon", "captured_at", "compass", "compass_raw",
           "heading_conf", "tier", "sequence", "image_path"]

def get(url, params=None, tries=5):
    # retry failed requests automatically, waiting longer each time (2, 4, 8, 16 s); also retry on network errors and timeouts
    for k in range(tries):
        try:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code not in (429, 500, 502, 503, 504):
                return r
        except (requests.ConnectionError, requests.Timeout):
            if k == tries - 1:
                raise
        if k < tries - 1:
            time.sleep(2 ** (k + 1))
    return r                                              # still 429 / 5xx after the retries, let the caller report it

# ---- 4a. Photo list: Mapillary cuts the map into tiles of about 2.4 km, one request gets the list of all photos in a tile (photos not downloaded yet) ----
def fetch_tile(t):
    r = get(f"https://tiles.mapillary.com/maps/vtp/mly1_public/2/{t.z}/{t.x}/{t.y}", {"access_token": TOKEN})
    if r.status_code in (204, 404):                       # no photos in this tile
        return b""
    r.raise_for_status()
    return r.content

def decode_tile(content, t):
    # vector tile to table; tile coords (0-4096) converted to lon/lat
    feats = []
    if content:
        layer = mapbox_vector_tile.decode(content, default_options={"y_coord_down": True}).get("image")
        feats = [f for f in (layer["features"] if layer else []) if f["geometry"]["type"] == "Point"]
    if not feats:
        return pd.DataFrame(columns=META_COLS)
    xy = np.array([f["geometry"]["coordinates"] for f in feats], dtype=float)
    b = mercantile.xy_bounds(t)
    mx = b.left + xy[:, 0] / 4096 * (b.right - b.left)
    my = b.top - xy[:, 1] / 4096 * (b.top - b.bottom)
    p = [f["properties"] for f in feats]
    return pd.DataFrame({
        "image_id": [str(q["id"]) for q in p],
        "lon": np.degrees(mx / R_EARTH),
        "lat": np.degrees(2 * np.arctan(np.exp(my / R_EARTH)) - np.pi / 2),
        "captured_at_ms": [q.get("captured_at") for q in p],
        "compass_raw": [q.get("compass_angle") for q in p],
        "is_pano": [bool(q.get("is_pano")) for q in p],
        "sequence": [q.get("sequence_id") for q in p],
        "quality": [q.get("quality_score") for q in p],
    })

def load_metadata(bbox):
    # for each tile the bbox touches: read it from cache if there, otherwise download and save to cache/mapillary_meta/
    parts = []
    for t in tqdm(list(mercantile.tiles(*bbox, zooms=[14])), desc="tiles"):
        rel = f"cache/mapillary_meta/z14_{t.x}_{t.y}.parquet"
        if fs.exists(gs(rel)):
            part = pd.read_parquet(gs(rel))
        else:
            part = decode_tile(fetch_tile(t), t)
            part.to_parquet(gs(rel), index=False)
        parts.append(part)
    return pd.concat(parts, ignore_index=True)

# ---- 4b. Filter and assign to cells: drop photos outside the bbox, too old, winter, panoramas, no heading, in that order; the "funnel" records how many are left after each step ----
def filter_photos(raw, bbox):
    min_lon, min_lat, max_lon, max_lat = bbox
    df = raw.copy()
    df[["lat", "lon"]] = df[["lat", "lon"]].astype(float)   # empty cached tiles stored lat/lon as text
    df["captured_at"] = pd.to_datetime(pd.to_numeric(df.captured_at_ms), unit="ms", utc=True)
    df["compass"] = pd.to_numeric(df.compass_raw)
    funnel = [("all photos in the tiles", len(df))]
    def keep(name, mask):
        nonlocal df
        df = df[mask(df)]
        funnel.append((name, len(df)))
    keep("inside the area", lambda d: d.lon.between(min_lon, max_lon) & d.lat.between(min_lat, max_lat))
    keep(f"taken in {MIN_YEAR} or later", lambda d: d.captured_at.dt.year >= MIN_YEAR)
    keep(f"not in months {EXCLUDE_MONTHS}", lambda d: ~d.captured_at.dt.month.isin(EXCLUDE_MONTHS))
    keep("not a panorama", lambda d: ~d.is_pano.astype(bool))
    keep("has a heading", lambda d: d.compass.notna())
    df = df.copy()
    df["compass"] = df.compass % 360
    df["h3_index"] = [h3.latlng_to_cell(la, lo, H3_RES) for la, lo in zip(df.lat, df.lon)]
    return df, funnel

# ---- 4c. Selection rules: position first, diverse headings (greedy MMR + relaxing step by step) ----
USE_SFM_HEADING = True       # ask Mapillary for the SfM heading: one request per photo, saved to cache, so each photo is only asked once
SFM_DIR = "cache/mapillary_sfm"                    # each save is a new file, so people running at the same time don't overwrite each other
SFM_OLD = "cache/mapillary_sfm_heading.parquet"    # the single file used before, still read
BETA = 0.15                  # edge band = 15% of the centre-to-edge distance (about 10 m at H3 res 10)
LAMBDA = 0.5                 # balance: 1 = base score only, 0 = diversity only
DUP_M, DUP_DEG = 30, 30      # same sequence + < 30 m apart + heading diff < 30° = near copy
TRAVEL_FRAMES = 5            # travel direction: from 5 frames before to 5 frames after (frames are only about 1 m apart)
CAND_PER_DIR = 8             # each cell is split into 4 groups (N/E/S/W) by phone compass, only the best 8 per group get an SfM heading lookup and go into selection

ang = lambda a, b: np.abs((np.asarray(a, dtype=float) - b + 180) % 360 - 180)    # smallest angle between two headings, 0-180°
SFM = None                   # all saved SfM headings; read once per run, then updated as we go

def sfm_headings(ids):
    # SfM heading: the heading Mapillary recomputes from many overlapping photos, more accurate than the phone compass
    # returns (SfM heading per photo, {photos not fetched: reason}). If Mapillary clearly says "photo does not exist", it counts as no SfM, not a failure
    global SFM
    if SFM is None:
        files = fs.find(f"{BUCKET}/{SFM_DIR}") + ([f"{BUCKET}/{SFM_OLD}"] if fs.exists(gs(SFM_OLD)) else [])
        with ThreadPoolExecutor(16) as ex:
            SFM = pd.concat([pd.DataFrame({"image_id": pd.Series(dtype=str), "sfm": pd.Series(dtype=float)})]
                            + list(ex.map(lambda p: pd.read_parquet(f"gs://{p}"), files)), ignore_index=True)
    todo = sorted(set(ids) - set(SFM.image_id))
    def ask(i):
        try:
            r = get(f"https://graph.mapillary.com/{i}", {"access_token": TOKEN, "fields": "computed_compass_angle"})
            if r.status_code == 404 or (r.status_code == 400 and "does not exist" in r.text):
                return np.nan                             # photo no longer exists: no SfM heading (not a failure)
            r.raise_for_status()
            v = r.json().get("computed_compass_angle")
            return np.nan if v is None else float(v)
        except Exception as e:
            return "failed: " + why(e)
    failed = {}
    for k in range(0, len(todo), 2000):                   # save every 2000 photos, so if it stops midway a rerun continues from there
        chunk = todo[k:k + 2000]
        with ThreadPoolExecutor(max_workers=16) as ex:
            got = list(tqdm(ex.map(ask, chunk), total=len(chunk), desc=f"SfM headings {k + len(chunk)}/{len(todo)}"))
        failed |= {i: v for i, v in zip(chunk, got) if isinstance(v, str)}
        new = pd.DataFrame([(i, v) for i, v in zip(chunk, got) if not isinstance(v, str)], columns=["image_id", "sfm"])
        new.to_parquet(gs(f"{SFM_DIR}/{datetime.datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}.parquet"), index=False)
        SFM = pd.concat([SFM, new], ignore_index=True)    # failed ones are not saved, they get asked again next time
    return SFM.drop_duplicates("image_id").set_index("image_id").sfm.reindex(ids).to_numpy(dtype=float), failed

def add_travel(df):
    # travel direction: direction from 5 frames before to 5 frames after in the same sequence, only counts if it moved more than 3 m (needs the whole sequence, so it is computed before shortlisting)
    df = df.sort_values(["sequence", "captured_at"]).copy()
    x = df.lon * M_LAT * np.cos(np.radians(df.lat.mean()))
    y = df.lat * M_LAT
    dx = x.groupby(df.sequence).shift(-TRAVEL_FRAMES) - x.groupby(df.sequence).shift(TRAVEL_FRAMES)
    dy = y.groupby(df.sequence).shift(-TRAVEL_FRAMES) - y.groupby(df.sequence).shift(TRAVEL_FRAMES)
    df["travel"] = pd.Series(np.degrees(np.arctan2(dx, dy)) % 360, index=df.index).where(np.hypot(dx, dy) > 3)
    return df

def shortlist(df, naip_of):
    # shortlist first: ignore heading, rank by "close to centre 0.65 + quality 0.15 + close to NAIP date 0.2", keep only the top CAND_PER_DIR per direction (far fewer heading requests)
    centre = {h: h3.cell_to_latlng(h) for h in df.h3_index.unique()}
    clat = df.h3_index.map(lambda h: centre[h][0]).astype(float)
    clon = df.h3_index.map(lambda h: centre[h][1]).astype(float)
    d = np.hypot((df.lon - clon) * M_LAT * np.cos(np.radians(clat)), (df.lat - clat) * M_LAT)
    years = ((df.captured_at - pd.to_datetime(df.h3_index.map(naip_of), utc=True)).abs().dt.days / 365.25).fillna(4)
    pre = 0.65 * (1 - d / 76) + 0.15 * pd.to_numeric(df.quality, errors="coerce").fillna(0.5) + 0.2 * (1 - np.minimum(years / 4, 1))
    return (df.assign(_pre=pre, _dir=(df.compass // 90).astype(int)).sort_values("_pre", ascending=False)
              .groupby(["h3_index", "_dir"]).head(CAND_PER_DIR).drop(columns=["_pre", "_dir"]))

def add_heading(df):
    # heading confidence: each photo gets its most trusted heading compass and a confidence heading_conf (raw compass stays in compass_raw)
    df = df.copy()
    df["compass_raw"] = df.compass
    sfm, failed = sfm_headings(df.image_id.tolist()) if USE_SFM_HEADING else (np.full(len(df), np.nan), {})
    has_sfm = ~np.isnan(sfm)
    agree = ang(df.compass_raw, df.travel) < 30           # compass agrees with travel direction = shot facing forward, trusted
    # priority: SfM angle (1.0) > compass that agrees with travel direction (0.7) > travel direction (0.4) > raw compass (0.4)
    df["compass"] = np.select([has_sfm, agree, df.travel.notna()], [sfm % 360, df.compass_raw, df.travel], df.compass_raw)
    df["heading_conf"] = np.select([has_sfm, agree], [1.0, 0.7], 0.4)
    return df, failed

def pick_by_position(g, k, naip_date):
    cell = g.h3_index.iloc[0]
    clat, clon = h3.cell_to_latlng(cell)
    to_xy = lambda la, lo: np.c_[(np.asarray(lo) - clon) * M_LAT * np.cos(np.radians(clat)), (np.asarray(la) - clat) * M_LAT]
    hexagon = Polygon(to_xy(*zip(*h3.cell_to_boundary(cell))))       # in meters, cell centre at (0, 0)
    r_c = hexagon.exterior.distance(Point(0, 0))                     # centre to edge, about 66 m
    R_c = np.hypot(*hexagon.exterior.coords[0])                      # centre to corner, about 76 m
    P = to_xy(g.lat, g.lon)
    g = g.assign(x=P[:, 0], y=P[:, 1], d=np.hypot(P[:, 0], P[:, 1]),
                 core=[hexagon.exterior.distance(Point(p)) >= BETA * r_c for p in P],   # core = not in the edge band
                 out=ang(g.compass, np.degrees(np.arctan2(-P[:, 0], -P[:, 1])) % 360) > 90)  # facing away from the cell centre

    # hard rule H1: in the edge band and confidently facing out = shooting the next cell, never pick
    g = g[~(~g.core & g.out & (g.heading_conf >= 0.7))]

    # step 1 base score: closer to centre is better (main part), then quality and closeness to the NAIP date; minus a bit for edge photos with unsure heading
    #   time score: same day as NAIP = 1, 2 years apart = 0.5, 4+ years apart = 0 (all 0 when the cell has no NAIP)
    years = (g.captured_at - pd.Timestamp(naip_date, tz="UTC")).abs().dt.days / 365.25 if isinstance(naip_date, str) else 4
    base = (0.65 * (1 - g.d / R_c) + 0.15 * pd.to_numeric(g.quality, errors="coerce").fillna(0.5)
            + 0.2 * (1 - np.minimum(years / 4, 1)) - 0.2 * ~g.core * (1 - g.heading_conf)).to_numpy()

    X, Y, H, C, S, core = (g[c].to_numpy() for c in ["x", "y", "compass", "heading_conf", "sequence", "core"])
    def sim(j):
        # how similar to picked photo j: heading 0.6 + same sequence 0.3 + position 0.1. Untrusted headings count as "same heading", so they don't get free diversity points
        return (0.6 * (1 - np.minimum(C, C[j]) * ang(H, H[j]) / 180) + 0.3 * (S == S[j])
                + 0.1 * np.clip(1 - np.hypot(X - X[j], Y - Y[j]) / 60, 0, None))
    def near_copy(j):
        # hard rule H2: same sequence, < 30 m apart, heading diff < 30° = near copy, can't be picked together with j
        return (S == S[j]) & (np.hypot(X - X[j], Y - Y[j]) < DUP_M) & (ang(H, H[j]) < DUP_DEG)

    # step 3 relax step by step: candidate pools go from strict to loose, open B only if A is not enough, C only if B is not enough; photos already picked stay
    #   A: core only, max 1 photo per sequence. No risk.
    #   B: still core only, the same sequence can be picked again (but no near copies). Only less independent.
    #   C: open the edge band (photos facing in or with unsure heading). Might show the next cell, so it is opened last.
    #   D: still fewer than MIN_IMAGES_PER_CELL after picking, so this cell gets no street view (the cell stays, aerial tile only). Don't force it.
    chosen, tiers = [], []
    blocked, max_sim = np.zeros(len(g), bool), np.zeros(len(g))
    for tier, pool in [("A", core), ("B", core), ("C", np.ones(len(g), bool))]:
        while len(chosen) < k:
            ok = pool & ~blocked
            if tier == "A":
                ok &= ~np.isin(S, S[chosen])
            if not ok.any():
                break
            # step 2 MMR: pick the photo with "high base score and most different from the picked photo it is most similar to"
            j = int(np.argmax(np.where(ok, LAMBDA * base - (1 - LAMBDA) * max_sim, -np.inf)))
            chosen.append(j); tiers.append(tier)
            blocked |= near_copy(j)                       # also blocks j itself
            max_sim = np.maximum(max_sim, sim(j))
    return g.iloc[chosen].assign(tier=tiers).drop(columns=["x", "y", "d", "core", "out"])

# ---- 4d. Pick photos, download, save ----
def download_image(image_id):
    # three results: "ok" downloaded (or already in cache); "deleted" Mapillary clearly says the photo does not exist (the data just isn't there, pick the next one);
    # "failed: reason" temporary problem like network / rate limit (this bbox is marked incomplete, a rerun only downloads these)
    # the whole image is downloaded into memory and checked that it opens fully before it goes into the bucket, so no empty files or error pages are left
    path = f"cache/mapillary_images/{image_id}.jpg"
    if fs.exists(gs(path)):
        return "ok"
    try:
        info = get(f"https://graph.mapillary.com/{image_id}", {"access_token": TOKEN, "fields": "thumb_1024_url"})
        if info.status_code == 404 or (info.status_code == 400 and "does not exist" in info.text):
            return "deleted"
        info.raise_for_status()
        url = info.json().get("thumb_1024_url")
        if not url:
            return "deleted"                              # this photo has no downloadable version
        img = get(url)                                    # image links expire, so ask for a new one every time
        img.raise_for_status()
        Image.open(io.BytesIO(img.content)).load()        # fully decode once: error pages or half-downloaded files fail here
        with fs.open(gs(path), "wb") as f:
            f.write(img.content)
        return "ok"
    except Exception as e:
        return "failed: " + why(e)

def street_view(cells):
    # for each city bbox: photo list, filter, shortlist, get headings, pick, download, save streetview/{area_id}.parquet
    # each bbox is saved as soon as it's done; finished ones are just read back, no picking or downloading again
    sv_list, funnels, street_reason = [], {}, {}          # street_reason: {cell_id: why this cell has / has no street view}
    for a in RUN:
        aid = a["area_id"]
        area = cells[cells.area_id == aid]
        if a["area_type"] != "city":                      # suburbs don't need street view
            mark(aid, "street_view", "not needed")
            street_reason |= dict.fromkeys(area.cell_id, "suburb")
            continue
        if step_status(aid, "naip") != "complete":        # picking needs the NAIP date, so skip for now if aerial tiles are not done
            mark(aid, "street_view", "incomplete", "waiting: aerial tiles are not complete for this bbox")
            continue
        rel, old_cells = f"{DS}/streetview/{aid}.parquet", f"{DS}/cells/{aid}.parquet"
        try:
            if fs.exists(gs(rel)) and fs.exists(gs(old_cells)):      # done before: read back photos and reasons
                old = pd.read_parquet(gs(old_cells))
                if "street_reason" in old:
                    sv_list.append(pd.read_parquet(gs(rel)))
                    street_reason |= dict(zip(old.cell_id, old.street_reason))
                    mark(aid, "street_view", "complete")
                    print(f"{aid}: already done")
                    continue
            df, funnels[aid] = filter_photos(load_metadata(a["bbox"]), a["bbox"])
            print(f"\n{aid}: filter funnel")
            for name, n in funnels[aid]:
                print(f"  {name:<28}{n:>10,}")
            df = df[df.h3_index.isin(area.h3_index)]      # only this bbox's cells (cells given to another bbox by dedup are dropped too)
            naip_of = area.set_index("h3_index").naip_date
            df, sfm_failed = add_heading(shortlist(add_travel(df), naip_of))
            if sfm_failed:                                # some photo headings were not fetched (network): don't pick, don't finalize
                raise Incomplete(f"{len(sfm_failed)} photo headings could not be fetched, e.g. {next(iter(sfm_failed.values()))}")

            # pick, then download. Photos Mapillary says no longer exist are dropped and that cell is picked again (up to 3 rounds)
            groups = dict(list(df.groupby("h3_index")))
            gone, result = set(), {}                      # result: image_id -> "ok" / "deleted" / "failed: ..."
            for _ in range(3):
                picks = [pick_by_position(g[~g.image_id.isin(gone)], MAX_IMAGES_PER_CELL, naip_of.get(h))
                         for h, g in groups.items() if (~g.image_id.isin(gone)).any()]
                p = pd.concat(picks) if picks else df.iloc[:0].assign(tier=None)
                new = [i for i in p.image_id if i not in result]
                with ThreadPoolExecutor(16) as ex:
                    result |= dict(zip(new, list(tqdm(ex.map(download_image, new), total=len(new), desc="photos"))))
                newly_gone = {i for i in p.image_id if result[i] == "deleted"} - gone
                if not newly_gone:
                    break
                gone |= newly_gone

            # some photos failed to download (network): don't save this bbox, record which photos and which cells
            failed = [dict(image_id=i, cell_id=f"{aid}_{h}", error=result[i])
                      for i, h in zip(p.image_id, p.h3_index) if result[i].startswith("failed")]
            if failed:
                raise Incomplete(f"{len(failed)} photos failed to download ({len({f['cell_id'] for f in failed})} cells), "
                                 f"e.g. {failed[0]['error']}", failed)

            p = p[p.image_id.map(result) == "ok"]
            p = p.assign(area_id=aid, cell_id=aid + "_" + p.h3_index.astype(str), image_path="cache/mapillary_images/" + p.image_id.astype(str) + ".jpg")
            p = p[p.groupby("cell_id").image_id.transform("size") >= MIN_IMAGES_PER_CELL]   # too few photos: no street view for this cell
            p["captured_at"] = p.captured_at.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            p = p[SV_COLS].sort_values(["cell_id", "image_id"]).reset_index(drop=True)
            p.to_parquet(gs(rel), index=False)
            sv_list.append(p)

            # write each cell's street view state: has_photos / no_photos (none left after filtering) / too_few_photos (fewer than MIN_IMAGES_PER_CELL) / photos_deleted (photos deleted by their author, still not enough after re-picking)
            has, lost = set(p.cell_id), set(df.h3_index[df.image_id.isin(gone)])
            for c, h in zip(area.cell_id, area.h3_index):
                street_reason[c] = ("has_photos" if c in has else "photos_deleted" if h in lost
                                    else "too_few_photos" if h in groups else "no_photos")
            mark(aid, "street_view", "complete", photos=len(p), photos_deleted_on_mapillary=len(gone))
            print(f"  {p.cell_id.nunique()} of {len(area)} cells have street photos ({len(p)} photos)")
        except Exception as e:                            # this bbox failed: mark incomplete (details of failed photos go into the status file), go on to the next
            mark(aid, "street_view", "incomplete", why(e), **({"failed": e.args[1]} if isinstance(e, Incomplete) and len(e.args) > 1 else {}))

    sv = pd.concat(sv_list, ignore_index=True) if sv_list else pd.DataFrame(columns=SV_COLS)
    cells["street_reason"] = cells.cell_id.map(street_reason)   # left empty for city bboxes that are not done
    print(f"\nStreet photos: {len(sv)} in {sv.cell_id.nunique()} cells")
    return sv, funnels


# ==================== 5. Map features (OpenStreetMap + WorldCover) ====================
# per cell, on the same GROUND_M square as the aerial tile: road length by class, bike lanes, intersection count, POI count, water / green / building share, WorldCover land class shares
CLASSES = {
    "major": ["motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link"],
    "secondary": ["secondary", "secondary_link", "tertiary", "tertiary_link"],
    "local": ["residential", "unclassified", "living_street", "service"],
    "path": ["cycleway", "footway", "path", "pedestrian", "track", "steps", "bridleway"],
}
HIGHWAY_CLASS = {h: k for k, hs in CLASSES.items() for h in hs}
NO_BIKE_LANE = ["no", "none", "separate", "shared_lane", "share_busway"]
WC = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built", 60: "bare",
      70: "snow", 80: "water", 90: "wetland", 95: "mangrove", 100: "moss"}

def get_pbf(url):
    # returns a local copy of the whole-state OSM map (pyrosm can only read local files); the master copy is in cache/osm/ in the bucket
    # write to .part first, rename when complete, so stopping midway won't leave half a file
    name = url.rsplit("/", 1)[-1]
    local = os.path.join("tmp", name)
    os.makedirs("tmp", exist_ok=True)
    if os.path.exists(local):
        return local
    if fs.exists(gs(f"cache/osm/{name}")):
        print(f"Copying {name} from GCS ...")
        fs.get(gs(f"cache/osm/{name}"), local + ".part")
        os.replace(local + ".part", local)
        return local
    print(f"Downloading {url} (a few hundred MB, only once) ...")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        last_modified = r.headers.get("last-modified")
        with open(local + ".part", "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    os.replace(local + ".part", local)
    fs.put(local, gs(f"cache/osm/{name}"))
    with fs.open(gs(f"cache/osm/{name}.json"), "w") as f:
        json.dump({"url": url, "downloaded_at": now(), "last_modified": last_modified}, f, indent=2)
    return local

def clip_pbf(pbf, w, s, e, n):
    # use osmium to clip the state map to a small piece around the cells (a few seconds), so pyrosm doesn't scan the whole state file 5 times; without osmium, read the whole file (slow, same result)
    if not shutil.which("osmium"):
        return pbf
    out = f"{pbf[:-len('.osm.pbf')]}_{w:.3f}_{s:.3f}_{e:.3f}_{n:.3f}.osm.pbf"
    if not os.path.exists(out):
        subprocess.run(["osmium", "extract", "-b", f"{w},{s},{e},{n}", pbf, "-o", out + ".part", "-f", "pbf", "--overwrite"],
                       check=True, capture_output=True)
        os.replace(out + ".part", out)
    return out

def area_frac(layer, fp):
    # what fraction of the square a polygon layer covers (overlaps counted once)
    if layer.empty:
        return 0.0
    near = layer.iloc[layer.sindex.query(fp, predicate="intersects")]
    return float(min(near.geometry.intersection(fp).union_all().area / fp.area, 1.0)) if len(near) else 0.0

def osm_features_for(area_cells, pbf_url):
    pts = gpd.GeoSeries(gpd.points_from_xy(area_cells.lon, area_cells.lat), crs=4326)
    utm = pts.estimate_utm_crs()
    foot = pts.to_crs(utm).buffer(GROUND_M / 2, cap_style="square").reset_index(drop=True)
    w, s, e, n = [float(v) for v in pts.total_bounds]
    bounds = [w - 0.004, s - 0.004, e + 0.004, n + 0.004]
    osm = pyrosm.OSM(clip_pbf(get_pbf(pbf_url), *bounds), bounding_box=bounds)

    # roads: split at every intersection, so intersections can be counted
    nodes, edges = osm.get_network(network_type="all", nodes=True,
                                   extra_attributes=["cycleway", "cycleway:left", "cycleway:right", "cycleway:both"])
    edges = edges.to_crs(utm).reset_index(drop=True)
    edges["rclass"] = edges.highway.map(HIGHWAY_CLASS).fillna("other")
    edges["bike"] = False
    for c in [c for c in edges.columns if c.startswith("cycleway")]:
        edges["bike"] |= edges[c].notna() & ~edges[c].isin(NO_BIKE_LANE)
    degree = pd.concat([edges.u, edges.v]).value_counts()           # how many road segments each node connects to
    nodes = nodes.to_crs(utm)
    junctions = nodes[nodes.id.map(degree).fillna(0) >= 3].reset_index(drop=True)

    def layer(tags):
        g = osm.get_data_by_custom_criteria(custom_filter=tags, filter_type="keep", keep_nodes=False, keep_ways=True, keep_relations=True)
        if g is None or len(g) == 0:
            return gpd.GeoDataFrame(geometry=[], crs=utm)
        g = g[["geometry"]].reset_index(drop=True).to_crs(utm)
        g["geometry"] = g.geometry.make_valid()          # fix broken OSM polygons like self-intersections, otherwise the area calc throws TopologyException
        return g
    water = layer({"natural": ["water", "wetland"], "waterway": ["riverbank", "dock"], "landuse": ["reservoir", "basin"]})
    green = layer({"leisure": ["park", "garden", "playground", "recreation_ground", "nature_reserve", "golf_course"],
                   "landuse": ["grass", "meadow", "forest", "village_green", "recreation_ground", "cemetery"],
                   "natural": ["wood", "scrub", "grassland"]})
    buildings = layer({"building": True})
    pois = osm.get_pois(custom_filter={"amenity": True, "shop": True, "tourism": True})
    pois = pois[["geometry"]].to_crs(utm).reset_index(drop=True) if pois is not None and len(pois) else gpd.GeoDataFrame(geometry=[], crs=utm)
    pois["geometry"] = pois.geometry.representative_point()         # area POIs (like a mall) only count once
    print(f"  OSM: {len(edges):,} road segments, {len(junctions):,} intersections, {len(buildings):,} buildings, {len(pois):,} POIs")

    rows = []
    for cid, fp in tqdm(list(zip(area_cells.cell_id, foot)), desc="cells"):
        near = edges.iloc[edges.sindex.query(fp, predicate="intersects")]
        length = near.geometry.intersection(fp).length               # length of each road inside the square (m)
        row = {"cell_id": cid}
        for k in [*CLASSES, "other"]:
            row[f"road_{k}_m"] = float(length[near.rclass == k].sum())
        row["road_total_m"] = float(length.sum())
        row["bike_lane_m"] = float(length[near.bike].sum())
        row["intersections"] = len(junctions.sindex.query(fp, predicate="intersects"))
        row["poi_count"] = len(pois.sindex.query(fp, predicate="intersects"))
        row["osm_water_frac"] = area_frac(water, fp)
        row["osm_green_frac"] = area_frac(green, fp)
        row["osm_building_frac"] = area_frac(buildings, fp)
        rows.append(row)
    feat = pd.DataFrame(rows)

    # WorldCover: share of each class among the 10 m land cover pixels in the square
    wc_items = list(catalog.search(collections=["esa-worldcover"], bbox=(w, s, e, n),
                                   datetime=f"{WORLDCOVER_YEAR}-01-01/{WORLDCOVER_YEAR}-12-31").items())
    foot_ll = foot.to_crs(4326)
    wc_rows = {}
    for it in wc_items:
        geom = shape(it.geometry)
        with rasterio.open(planetary_computer.sign(it.assets["map"].href)) as src:
            for cid, p, fl in zip(area_cells.cell_id, pts, foot_ll):
                if cid in wc_rows or not geom.contains(p):
                    continue
                x0, y0, x1, y1 = fl.bounds
                r0, c0 = src.index(x0, y1); r1, c1 = src.index(x1, y0)
                arr = src.read(1, window=Window(c0, r0, max(c1 - c0 + 1, 1), max(r1 - r0 + 1, 1)))
                counts = np.bincount(arr.ravel(), minlength=101)
                valid = sum(counts[c] for c in WC)                    # NoData (class 0) not counted
                wc_rows[cid] = {"cell_id": cid, **{f"wc_{name}_frac": (float(counts[c] / valid) if valid else np.nan) for c, name in WC.items()}}
    wc = pd.DataFrame(list(wc_rows.values()), columns=["cell_id", *[f"wc_{n}_frac" for n in WC.values()]])
    return feat.merge(wc, on="cell_id", how="left")

def url_exists(url):
    # ask Geofabrik if this file exists: yes = True; clearly not = False; no answer after 4 tries = None (no error, the caller tries another year)
    for k in range(4):
        try:
            code = requests.head(url, timeout=60, allow_redirects=True).status_code
            if code in (200, 404):
                return code == 200
        except requests.RequestException:
            pass
        time.sleep(15 * (k + 1))
    print(f"  Geofabrik did not answer for {url.rsplit('/', 1)[-1]}: trying an older year")
    return None

def osm_snapshot_url(latest_url, naip_day):
    # which year's OSM snapshot to use (Geofabrik keeps one per state every Jan 1):
    # 1) already in the bucket and within 2 years of the NAIP date: use the one closest to NAIP, no network
    # 2) none: look on Geofabrik in the order "closest to NAIP, then older, then newer", use the first one that exists
    # 3) nothing works: return None, this bbox is marked incomplete
    region = latest_url.rsplit("/", 1)[-1][:-len("-latest.osm.pbf")]
    snap = lambda d: latest_url.replace("-latest.osm.pbf", f"-{d:%y%m%d}.osm.pbf")
    cached = [pd.Timestamp(datetime.datetime.strptime(m[1], "%y%m%d")) for p in fs.find(f"{BUCKET}/cache/osm")
              if (m := re.fullmatch(rf"{re.escape(region)}-(\d{{6}})\.osm\.pbf", p.rsplit("/", 1)[-1]))]
    near = [d for d in cached if abs(d - naip_day) <= pd.Timedelta(days=731)]
    if near:
        return snap(min(near, key=lambda d: abs(d - naip_day)))
    jan1 = [pd.Timestamp(y, 1, 1) for y in range(naip_day.year - 3, naip_day.year + 3) if pd.Timestamp(y, 1, 1) < pd.Timestamp.now()]
    best = min(jan1, key=lambda d: abs(d - naip_day))
    for d in [best] + sorted([d for d in jan1 if d < best], reverse=True) + sorted(d for d in jan1 if d > best):
        if url_exists(snap(d)):
            return snap(d)
    return None

def map_features(cells):
    # Geofabrik's region index (state borders + download URLs), used to find which state a bbox is in; use the bucket copy if there, download it once otherwise
    idx = "cache/osm/geofabrik_index.json"
    if not fs.exists(gs(idx)):
        with fs.open(gs(idx), "w") as f:
            f.write(requests.get("https://download.geofabrik.de/index-v1.json", timeout=120).text)
    regions = [(shape(f["geometry"]), f["properties"]["urls"]["pbf"]) for f in json.load(fs.open(gs(idx)))["features"]
               if "/north-america/us/" in f["properties"]["urls"].get("pbf", "")]

    # one file per bbox; ones computed before are just read back, not recomputed
    feat_list = []
    cells["osm_url"] = None
    for a in RUN:
        aid = a["area_id"]
        area = cells[cells.area_id == aid]
        rel = f"{DS}/osm_features/{aid}.parquet"
        if step_status(aid, "naip") != "complete":        # picking a snapshot needs the NAIP date, so skip for now if aerial tiles are not done
            mark(aid, "osm", "incomplete", "waiting: aerial tiles are not complete for this bbox")
            continue
        try:
            # smallest region containing the bbox centre (usually a state, California is split into SoCal / NorCal)
            centre = Point((a["bbox"][0] + a["bbox"][2]) / 2, (a["bbox"][1] + a["bbox"][3]) / 2)
            hits = [(g.area, url) for g, url in regions if g.contains(centre)]
            assert hits, f"no Geofabrik region contains {a['bbox']} (NAIP and this lookup only cover the US)"
            latest = min(hits)[1]
            url_of = {d: osm_snapshot_url(latest, pd.Timestamp(d)) for d in area.naip_date.dropna().unique()}
            cells.loc[area.index, "osm_url"] = area.naip_date.map(url_of).where(area.naip_date.notna(), latest)
            if not fs.exists(gs(rel)):
                if cells.loc[area.index, "osm_url"].isna().any():
                    raise Incomplete("no OSM snapshot could be found, or Geofabrik did not answer")
                parts = []
                for url, group in cells.loc[area.index].groupby("osm_url"):     # usually one bbox uses only one snapshot
                    print(f"{aid}: {len(group)} cells use {url.rsplit('/', 1)[-1]}")
                    parts.append(osm_features_for(group.reset_index(drop=True), url))
                f = pd.concat(parts, ignore_index=True)
                f.insert(1, "h3_index", f.cell_id.map(cells.set_index("cell_id").h3_index))
                f.to_parquet(gs(rel), index=False)
            feat_list.append(pd.read_parquet(gs(rel)))
            mark(aid, "osm", "complete", snapshots=sorted({u.rsplit("/", 1)[-1] for u in cells.loc[area.index, "osm_url"].dropna()}))
        except Exception as e:
            mark(aid, "osm", "incomplete", why(e))

    feats = pd.concat(feat_list, ignore_index=True) if feat_list else pd.DataFrame(columns=["cell_id", "h3_index"])
    print(f"{len(feats)} cells, {feats.shape[1] - 2} feature columns")
    if len(feats):
        print(feats.describe().T[["mean", "min", "50%", "max"]].round(2).to_string())


# ==================== 6. Save + summary ====================
def read_all(sub):
    # read the files of all bboxes in the bucket (no matter who ran them), 16 at a time
    with ThreadPoolExecutor(16) as ex:
        parts = list(ex.map(lambda p: pd.read_parquet(f"gs://{p}"), fs.find(f"{BUCKET}/{DS}/{sub}")))
    return pd.concat(parts, ignore_index=True) if parts else None

def save(cells, sv, funnels):
    # one cells file per bbox; only save bboxes whose aerial tile step is done (aerial info in the cell table must be complete)
    cells["updated_at"] = now()
    for area_id, part in cells.groupby("area_id"):
        if step_status(area_id, "naip") == "complete":
            part.to_parquet(gs(f"{DS}/cells/{area_id}.parquet"), index=False)

    # save each bbox's status to status/{area_id}.json: old records are kept, steps run this time are overwritten with the new results
    for a in RUN:
        srel = f"{DS}/status/{a['area_id']}.json"
        old = json.load(fs.open(gs(srel))) if fs.exists(gs(srel)) else {"steps": {}}
        with fs.open(gs(srel), "w") as f:
            json.dump({"area_id": a["area_id"], "updated_at": now(), "steps": {**old["steps"], **STEPS.get(a["area_id"], {})}},
                      f, indent=2, ensure_ascii=False, default=str)

    # summarize the whole dataset (reads all bboxes in the bucket, not just this run, so whoever runs last gets a complete summary)
    fs.invalidate_cache()                                 # clear the file listing cache so files just written show up
    cells_all = read_all("cells")
    cells_all = cells.iloc[:0] if cells_all is None else cells_all
    sv_all = read_all("streetview")
    sv_all = sv.iloc[:0] if sv_all is None else sv_all
    with ThreadPoolExecutor(16) as ex:
        status_all = {s["area_id"]: s["steps"] for s in ex.map(lambda p: json.load(fs.open(p)), fs.find(f"{BUCKET}/{DS}/status"))}
    def bbox_state(area_id):
        # complete / incomplete (with reason) / not run yet
        steps = status_all.get(area_id)
        if steps is None:
            return "not run yet", ""
        bad = {k: v.get("reason", "") for k, v in steps.items() if v["status"] == "incomplete"}
        return ("incomplete", "; ".join(f"{k}: {r}" for k, r in bad.items())) if bad else ("complete", "")

    # areas.parquet: one row per bbox in the config: status, cell count, aerial tile count, street view count, counts of each kind of "data just isn't there" cell, NAIP dates and OSM used
    join_unique = lambda s: ", ".join(sorted(s.dropna().unique()))
    areas = pd.DataFrame([dict(area_id=a["area_id"], area_type=a["area_type"], area_subtype=a["area_subtype"], bbox=str(a["bbox"])) for a in AREAS])
    areas["status"], areas["incomplete_reason"] = zip(*[bbox_state(i) for i in areas.area_id])
    areas = (areas.join(cells_all.groupby("area_id").agg(cells=("cell_id", "size"), tiles_ok=("tile_ok", "sum"),
                                                         naip_dates=("naip_date", join_unique), osm_urls=("osm_url", join_unique)), on="area_id")
                  .join(sv_all.groupby("area_id").agg(street_cells=("cell_id", "nunique"), street_photos=("image_id", "size")), on="area_id"))
    for col, prefix in [("tile_reason", "tile:"), ("street_reason", "street:")]:
        if col in cells_all and cells_all[col].notna().any():
            areas = areas.join(cells_all.groupby("area_id")[col].value_counts().unstack(fill_value=0).add_prefix(prefix), on="area_id")
    num = [c for c in areas.columns if c in ("cells", "tiles_ok", "street_cells", "street_photos") or c.startswith(("tile:", "street:"))]
    areas[num] = areas[num].fillna(0).astype(int)
    areas.to_parquet(gs(f"{DS}/areas.parquet"), index=False)

    # manifest.json: progress, totals, data sources
    osm_meta = lambda url: f"cache/osm/{url.rsplit('/', 1)[-1]}.json"
    osm_sources = [json.load(fs.open(gs(osm_meta(u)))) if fs.exists(gs(osm_meta(u))) else {"url": u} for u in sorted(cells_all.osm_url.dropna().unique())]
    manifest = json.load(fs.open(gs(f"{DS}/manifest.json")))
    manifest.update({
        "status": f"{(areas.status == 'complete').sum()}/{len(AREAS)} bboxes complete", "updated_at": now(),
        "results": {"cells": len(cells_all), "tiles_ok": int(cells_all.tile_ok.sum()), "street_cells": int(sv_all.cell_id.nunique()), "street_photos": len(sv_all),
                    "filter_funnel": {**manifest.get("results", {}).get("filter_funnel", {}), **{k: dict(f) for k, f in funnels.items()}}},
        "sources": {"osm": osm_sources, "worldcover_year": WORLDCOVER_YEAR,
                    "attribution": ["Street-level imagery © Mapillary contributors, CC-BY-SA 4.0",
                                    "© OpenStreetMap contributors (ODbL)", "ESA WorldCover (CC BY 4.0)", "NAIP (USDA, public domain)"]},
    })
    with fs.open(gs(f"{DS}/manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)

    print(f"\nDataset written to gs://{BUCKET}/{DS}/")
    fs.invalidate_cache()
    for sub in ["cells", "osm_features", "streetview", "status"]:
        print(f"  {sub + '/':<16}{len(fs.find(f'{BUCKET}/{DS}/{sub}')):>6} files")
    print("\n" + areas.drop(columns=["bbox"]).to_string(index=False))

    # incomplete list: bboxes not done because of network etc., with reasons and rerun args you can copy
    bad = areas[areas.status == "incomplete"]
    if len(bad):
        print(f"\n!!! {len(bad)} bbox(es) NOT complete (rerun them; finished parts are not redone):")
        for r in bad.itertuples():
            print(f"    {r.area_id:<28} {r.incomplete_reason}")
        print(f"Rerun these (only your own): --areas {' '.join(bad.area_id)}")
    else:
        print("\nNo incomplete bboxes.")
    waiting = areas.area_id[areas.status == "not run yet"].tolist()
    if waiting:
        print(f"Not run yet (maybe a teammate's): {waiting}")


# ==================== 7. QC figures (saved to local qc/ and {DS}/qc/ in the bucket) ====================
def thumb(path, px):
    # small thumbnails embedded in the web page (the bucket is private, the browser can't read images directly)
    with fs.open(gs(path), "rb") as fh:
        im = Image.open(fh).convert("RGB")
    im.thumbnail((px, px)); buf = io.BytesIO(); im.save(buf, "JPEG", quality=62)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

def save_fig(name):
    plt.savefig(f"{QC_DIR}/{name}", dpi=100, bbox_inches="tight")
    plt.close()

def qc(cells, sv):
    os.makedirs(QC_DIR, exist_ok=True)

    # tiles.png: 4 aerial tiles per bbox
    show = cells[cells.tile_ok].groupby("area_id").head(4)
    rows = max(1, -(-len(show) // 4))
    fig, axes = plt.subplots(rows, 4, figsize=(12, 3.1 * rows), squeeze=False)
    for ax in axes.ravel(): ax.axis("off")
    for ax, r in zip(axes.ravel(), show.itertuples()):
        ax.imshow(read_png(r.tile_path))
        ax.set_title(f"{r.area_id} · NAIP {r.naip_date}", fontsize=8)
    save_fig("tiles.png")

    # areas.png: one small map per bbox, cells colored by state: has street view / city without street view / suburb
    status = np.where(cells.cell_id.isin(sv.cell_id), 2, np.where(cells.area_type == "city", 1, 0))
    colors = np.array(["#9db4c8", "#f0b04a", "#2d8f6d"])
    names = ["suburb: aerial tile only", "city: no street photos", "has street photos"]
    rows = -(-len(RUN) // 4)
    fig, axes = plt.subplots(rows, min(4, len(RUN)), figsize=(5 * min(4, len(RUN)), 5 * rows), squeeze=False)
    for ax in axes.ravel(): ax.axis("off")
    for ax, a in zip(axes.ravel(), RUN):
        sel = (cells.area_id == a["area_id"]).to_numpy()
        hexagons = [[(lon, lat) for lat, lon in h3.cell_to_boundary(h)] for h in cells.h3_index[sel]]
        ax.add_collection(PolyCollection(hexagons, facecolors=colors[status[sel]], edgecolors="white", linewidths=0.5))
        ax.autoscale_view(); ax.set_aspect(1 / np.cos(np.radians(a["bbox"][1]))); ax.axis("off")
        ax.set_title(f"{a['area_id']} ({a['area_subtype']}): {sel.sum()} cells", fontsize=10)
    fig.legend(handles=[Patch(facecolor=colors[i], label=names[i]) for i in (2, 1, 0)], loc="lower center", ncol=3, frameon=False, fontsize=9)
    save_fig("areas.png")

    if sv.empty:
        print("No street photos: map skipped.")
    else:
        # photos.png: photos from two random cells that have street view
        show = sv.cell_id.drop_duplicates().sample(n=min(2, sv.cell_id.nunique()), random_state=0)
        fig, axes = plt.subplots(len(show), MAX_IMAGES_PER_CELL, figsize=(3 * MAX_IMAGES_PER_CELL, 2.4 * len(show)), squeeze=False)
        for ax in axes.ravel(): ax.axis("off")
        for i, cid in enumerate(show):
            for j, r in enumerate(sv[sv.cell_id == cid].head(MAX_IMAGES_PER_CELL).itertuples()):
                with fs.open(gs(r.image_path), "rb") as f:
                    axes[i, j].imshow(Image.open(f))
                axes[i, j].set_title(f"{cid[-5:]} · heading {r.compass:.0f}° · {r.captured_at[:7]}", fontsize=7)
        save_fig("photos.png")
        street_map(cells, sv)

    for name in os.listdir(QC_DIR):
        fs.put(f"{QC_DIR}/{name}", gs(f"{DS}/{QC_DIR}/{name}"))
    print(f"QC files: local {QC_DIR}/ and gs://{BUCKET}/{DS}/{QC_DIR}/")

def street_map(cells, sv):
    # map.html interactive map: basemap + bbox borders + all cells (filled = has street view) + filtered photo points (color = year) + camera direction of picked photos
    # layer toggles in the top right; big bboxes only draw some of the photo points, so the file isn't too big for the browser to open
    ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
    ARROW_M = 30                 # arrow length (m)
    MAP_MAX_POINTS = 4000        # max number of "filtered photo points" drawn per bbox (display only)
    SAMPLE_GRID = 3              # sample to view: each city bbox is cut into 3x3 blocks, 1 random cell with street view per block (max 9)
    SAMPLE_SEED = 0              # change this number to get a different sample
    corners = lambda b: [[b[1], b[0]], [b[3], b[2]]]
    rnd = lambda v: round(float(v), 6)

    # filtered photos (re-read from cached tiles, nothing new downloaded); a random subset for big bboxes
    parts, n_all = [], 0
    for a in RUN:
        if step_status(a["area_id"], "street_view") != "complete":
            continue
        f = filter_photos(load_metadata(a["bbox"]), a["bbox"])[0]
        n_all += len(f)
        parts.append(f.sample(MAP_MAX_POINTS, random_state=0) if len(f) > MAP_MAX_POINTS else f)
    kept = pd.concat(parts, ignore_index=True)
    years = sorted(kept.captured_at.dt.year.unique())
    year_color = {int(y): to_hex(plt.get_cmap("viridis")(i / max(len(years) - 1, 1))) for i, y in enumerate(years)}

    # which way each picked photo faces, compared to the direction toward the cell centre
    lat0 = sv.lat.mean()
    m_lat, m_lon = 110574.0, 111320.0 * np.cos(np.radians(lat0))
    centre = {h: h3.cell_to_latlng(h) for h in sv.h3_index.unique()}
    dx = (sv.h3_index.map(lambda h: centre[h][1]) - sv.lon) * m_lon
    dy = (sv.h3_index.map(lambda h: centre[h][0]) - sv.lat) * m_lat
    off = np.abs((sv.compass - np.degrees(np.arctan2(dx, dy)) % 360 + 180) % 360 - 180)
    away = (off > 90) & (np.hypot(dx, dy) >= 15)          # within 15 m of the centre, any direction counts as facing in
    print(f"{int(away.sum())} of {len(sv)} selected photos ({away.mean():.0%}) point away from their cell centre "
          f"(random directions would give about 50%)")

    m = folium.Map(tiles=None, prefer_canvas=True)        # canvas can draw 10k+ shapes without lag
    folium.TileLayer(ESRI.format("World_Imagery"), attr="Esri", name="Basemap: satellite", overlay=True, show=True).add_to(m)
    folium.TileLayer(ESRI.format("World_Street_Map"), attr="Esri", name="Basemap: street map", overlay=True, show=False).add_to(m)

    # cells (bottom layer): cells without street view are transparent but still "filled", so hovering anywhere in a cell shows the tooltip
    def hex_ring(h):
        ring = [[rnd(lo), rnd(la)] for la, lo in h3.cell_to_boundary(h)]
        return [ring + [ring[0]]]
    n_photos = sv.groupby("cell_id").size()
    cell_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"cell": r.cell_id, "photos": int(n_photos.get(r.cell_id, 0))},
         "geometry": {"type": "Polygon", "coordinates": hex_ring(r.h3_index)}} for r in cells.itertuples()]}
    folium.GeoJson(cell_fc, name="H3 cells (filled = has street photos)",
                   style_function=lambda f: {"color": "#ffffff", "weight": 1, "opacity": 0.8, "fill": True,
                                             "fillColor": "#2d8f6d", "fillOpacity": 0.25 if f["properties"]["photos"] else 0},
                   highlight_function=lambda f: {"weight": 3, "color": "#ffea00"},
                   tooltip=folium.GeoJsonTooltip(["cell", "photos"], aliases=["cell", "street photos"])).add_to(m)

    # bbox borders drawn as lines (a rectangle would block the tooltips of the cells inside)
    area_layer = folium.FeatureGroup(name="Areas (red = city, blue = suburb)").add_to(m)
    for a in RUN:
        lo0, la0, lo1, la1 = a["bbox"]
        folium.PolyLine([(la0, lo0), (la0, lo1), (la1, lo1), (la1, lo0), (la0, lo0)], color="#d1342a" if a["area_type"] == "city" else "#2f6f9f",
                        weight=2, tooltip=f"{a['area_id']} · {a['area_subtype']}").add_to(area_layer)

    # filtered photos: one layer of points, color = year
    points = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [rnd(lo), rnd(la)]}, "properties": {"year": int(y)}}
        for lo, la, y in zip(kept.lon, kept.lat, kept.captured_at.dt.year)]}
    folium.GeoJson(points, name=f"Photos after filtering ({len(kept):,} shown of {n_all:,}; color = year)",
                   marker=folium.CircleMarker(radius=2, weight=0, fill=True, fill_opacity=0.7),
                   style_function=lambda f: {"fillColor": year_color[f["properties"]["year"]]}).add_to(m)

    # picked photos: arrows + points with tooltips (green = facing into the cell, red = facing out)
    arrow_f, dot_f = [], []
    for r, is_away in zip(sv.itertuples(), away):
        color = "#d1342a" if is_away else "#1b9e5a"
        ang_ = np.radians(r.compass)
        tip = (r.lat + np.cos(ang_) * ARROW_M / m_lat, r.lon + np.sin(ang_) * ARROW_M / m_lon)
        heads = [(tip[0] - np.cos(ang_ + s) * 8 / m_lat, tip[1] - np.sin(ang_ + s) * 8 / m_lon) for s in (0.5, -0.5)]
        xy = lambda p: [rnd(p[1]), rnd(p[0])]
        arrow_f.append({"type": "Feature", "properties": {"c": color},
                        "geometry": {"type": "MultiLineString", "coordinates": [[xy((r.lat, r.lon)), xy(tip)], [xy(heads[0]), xy(tip), xy(heads[1])]]}})
        dot_f.append({"type": "Feature", "properties": {"c": color, "info": f"{r.cell_id} · {r.compass:.0f}° · {'away' if is_away else 'into the cell'}"},
                      "geometry": {"type": "Point", "coordinates": [rnd(r.lon), rnd(r.lat)]}})
    sel = folium.FeatureGroup(name=f"{len(sv):,} selected photos: camera direction").add_to(m)
    folium.GeoJson({"type": "FeatureCollection", "features": arrow_f},
                   style_function=lambda f: {"color": f["properties"]["c"], "weight": 2.5}).add_to(sel)
    folium.GeoJson({"type": "FeatureCollection", "features": dot_f},
                   marker=folium.CircleMarker(radius=3, weight=1, fill=True, fill_opacity=1),
                   style_function=lambda f: {"color": f["properties"]["c"], "fillColor": f["properties"]["c"]},
                   tooltip=folium.GeoJsonTooltip(["info"], labels=False)).add_to(sel)

    # sample cells: each city bbox is cut into 3x3 blocks, 1 random cell with street view per block; click one to pop up its aerial tile and picked street photos
    tile_of = cells.set_index("cell_id")
    rng = np.random.default_rng(SAMPLE_SEED); samples = []
    for a in RUN:
        if a["area_type"] != "city":
            continue
        cand = cells[(cells.area_id == a["area_id"]) & cells.cell_id.isin(sv.cell_id)]
        if cand.empty:
            continue
        lo0, la0, lo1, la1 = a["bbox"]
        bx = np.minimum(((cand.lon - lo0) / (lo1 - lo0) * SAMPLE_GRID).astype(int), SAMPLE_GRID - 1)
        by = np.minimum(((cand.lat - la0) / (la1 - la0) * SAMPLE_GRID).astype(int), SAMPLE_GRID - 1)
        for _, blk in cand.groupby([bx, by]):
            samples.append(blk.iloc[int(rng.integers(len(blk)))])
    samples = pd.DataFrame(samples)
    jobs = [(r.cell_id, "tile", r.tile_path, 170) for r in samples.itertuples() if r.tile_ok] + \
           [(p.cell_id, p.image_id, p.image_path, 200) for p in sv[sv.cell_id.isin(samples.cell_id)].itertuples()]
    with ThreadPoolExecutor(16) as ex:
        pics = dict(zip([(j[0], j[1]) for j in jobs], ex.map(lambda j: thumb(j[2], j[3]), jobs)))
    smp = folium.FeatureGroup(name=f"抽样格子：点开看图（{len(samples)} 个，黄色）").add_to(m)
    for r in samples.itertuples():
        ph = sv[sv.cell_id == r.cell_id].sort_values("compass")
        body = (f'<div style="font:12px sans-serif;width:830px"><b>{html.escape(r.cell_id)}</b> · {len(ph)} 张街景'
                + (f'<div style="margin:4px 0"><img src="{pics[(r.cell_id, "tile")]}" style="width:170px"><br>航拍图（{GROUND_M} m）</div>' if (r.cell_id, "tile") in pics else "")
                + '<div style="display:flex;flex-wrap:wrap;gap:4px">'
                + "".join(f'<div style="width:200px"><img src="{pics[(p.cell_id, p.image_id)]}" style="width:200px"><br>{p.compass:.0f}° · {str(p.captured_at)[:7]}</div>' for p in ph.itertuples())
                + "</div></div>")
        folium.Polygon([list(q) for q in h3.cell_to_boundary(r.h3_index)], color="#ff6a00", weight=3, fill=True, fill_color="#ffd400", fill_opacity=0.6,
                       tooltip=f"{r.cell_id} · 点开看图", popup=folium.Popup(body, max_width=860)).add_to(smp)
    print(f"Sample cells with pictures: {len(samples)} in {samples.area_id.nunique()} city bboxes")

    # dropdown in the top left: pick a bbox and the view jumps there
    bounds = {a["area_id"]: corners(a["bbox"]) for a in RUN}
    opts = "".join(f'<option value="{a["area_id"]}">{a["area_id"]}</option>' for a in sorted(RUN, key=lambda a: (a["area_type"] != "city", a["area_id"])))
    m.get_root().html.add_child(folium.Element(
        '<div style="position:fixed;top:80px;left:10px;z-index:9999;background:white;padding:6px 9px;border-radius:6px;font:12px sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3)">'
        f'<b>跳到区域</b><br><select onchange="gpZoom(this.value)"><option value="">（全部）</option>{opts}</select></div>'))
    m.get_root().script.add_child(folium.Element(
        f"var GP_BOUNDS = {json.dumps(bounds)};"
        f"function gpZoom(id) {{ var b = id ? GP_BOUNDS[id] : Object.values(GP_BOUNDS).flat(); {m.get_name()}.fitBounds(id ? b : L.latLngBounds(b), {{padding: [20, 20]}}); }}"))

    legend = "".join(f'<span style="color:{c}">●</span> {y} ' for y, c in year_color.items())
    m.get_root().html.add_child(folium.Element(
        f'<div style="position:fixed;bottom:18px;left:10px;z-index:9999;background:white;padding:6px 9px;border-radius:6px;'
        f'font:12px sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3)">Photo year: {legend}<br>'
        f'<span style="color:#1b9e5a">━▶</span> points into its cell &nbsp; <span style="color:#d1342a">━▶</span> points away<br><span style="background:#ffd400;border:2px solid #ff6a00;padding:0 6px"></span> 抽样格子：点开看航拍图和街景</div>'))
    folium.LayerControl(collapsed=False).add_to(m)
    b = np.array([a["bbox"] for a in RUN])
    m.fit_bounds(corners((b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max())))
    m.save(f"{QC_DIR}/map.html")
    print(f"Map saved: {QC_DIR}/map.html ({os.path.getsize(f'{QC_DIR}/map.html') / 1e6:.1f} MB). Open it in Chrome / Safari.")


# ==================== Entry point ====================
def main():
    load_dotenv(find_dotenv(usecwd=True))                 # locally reads .env; in the container it's passed with --env-file, fine if there's no .env
    p = argparse.ArgumentParser(description="GreenPath data collector")
    p.add_argument("--config", required=True, help="配置文件：本地路径或 gs://")
    p.add_argument("--areas", nargs="+", required=True, help="这次跑哪些 bbox（area_id，可以写多个），或 all")
    p.add_argument("--dataset", help="数据集名 datasets/<dataset>/（不写 = 用配置文件里的 dataset_id）")
    p.add_argument("--bucket", default=os.environ.get("GREENPATH_BUCKET"), help="默认读环境变量 GREENPATH_BUCKET")
    p.add_argument("--no-qc", action="store_true", help="不画检查用的图和地图（通宵跑时用）")
    args = p.parse_args()
    if not args.bucket:
        p.error("no bucket: pass --bucket or set GREENPATH_BUCKET")

    setup(args)
    cells = make_cells();                   tick("grid")
    items, near = find_naip(cells)
    crop_tiles(cells, items, near);         tick("aerial tiles")
    sv, funnels = street_view(cells);       tick("street view")
    map_features(cells);                    tick("OSM")
    save(cells, sv, funnels);               tick("save")
    if not args.no_qc:
        qc(cells, sv);                      tick("qc")
    print("\nTime per step (minutes):", ", ".join(f"{k} {v:.1f}" for k, v in TIMES.items()), f"| total {(time.time() - T0) / 60:.1f}")


if __name__ == "__main__":
    main()
