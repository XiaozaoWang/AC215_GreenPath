"""
GreenPath 数据采集（原 01 notebook）：把配置文件里的 bbox 整理成一个数据集。
每个格子一张航拍图（NAIP）+ 地图特征（OSM + WorldCover）；城市 bbox 再配街景照片（Mapillary）。不打分（打分在 data-labeler）。

用法：
  python cli.py --config configs/1026_Full_data.yaml --areas city_ma_boston_big sub_ma_hadley_farm
  python cli.py --config configs/1026_Full_data.yaml --areas all --no-qc

做过的会自动跳过，断了重跑会接着做。和组员分工：同一个配置文件，各自在 --areas 里写自己负责的 bbox。
"""
import argparse, base64, datetime, html, io, json, os, re, shutil, subprocess, time, uuid, warnings
from concurrent.futures import ThreadPoolExecutor

import fsspec, gcsfs, h3, mapbox_vector_tile, mercantile, requests, yaml
import numpy as np, pandas as pd
import geopandas as gpd, pyrosm, planetary_computer, pystac_client, rasterio
import folium
import matplotlib
matplotlib.use("Agg")                                     # 没有屏幕：图只存成文件
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
MAX_NODATA = 0.05                                         # 航拍图黑色空白像素不超过 5% 才算能用
CROP_WORKERS = 8                                          # 同时裁几张航拍图

fs = gcsfs.GCSFileSystem()
gs = lambda rel: f"gs://{BUCKET}/{rel}"                   # bucket 里的相对路径 → 完整 gs:// 路径
now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ==================== 状态记录 ====================
# 每个 bbox 每一步只有三种结果：complete（做完）/ incomplete（网络等暂时问题没做完，要重跑）/ not needed（不适用，比如郊区不找街景）
# “本来就没有”（没人拍过、没有 NAIP 覆盖……）不算 incomplete，写在每个格子的 tile_reason / street_reason 里
STEPS = {}                                                # {area_id: {step: {"status": ..., "reason": ..., 其他明细}}}

class Incomplete(Exception):
    pass                                                  # 代码自己判断“没做完”时用：Incomplete(原因, 明细)

def mark(area_id, step, status, reason="", **detail):
    STEPS.setdefault(area_id, {})[step] = {"status": status, **({"reason": reason} if reason else {}), **detail}
    if status == "incomplete":
        print(f"!!! {area_id}: {step} NOT complete: {reason}")

def step_status(area_id, step):
    return STEPS.get(area_id, {}).get(step, {}).get("status")

def why(e):
    # 把报错翻译成一句原因：网络 / 限流 / 服务器忙 / 没权限；其他情况给出原始报错（多半是代码问题）
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

# 计时：每个大步骤结束时打印这一步和总共用了几分钟
T0 = T_LAST = time.time()
TIMES = {}
def tick(step):
    global T_LAST
    TIMES[step] = (time.time() - T_LAST) / 60
    T_LAST = time.time()
    print(f"⏱ {step}: {TIMES[step]:.1f} min (total {(T_LAST - T0) / 60:.1f} min)")


# ==================== 1. 配置 + 连接 bucket ====================
def mapillary_token():
    # 本地：.env（或 docker --env-file）里的 MAPILLARY_TOKEN；云上：从 group project 的 Secret Manager 取 mapillary-token
    if os.environ.get("MAPILLARY_TOKEN"):
        return os.environ["MAPILLARY_TOKEN"]
    import google.auth
    from google.cloud import secretmanager
    project = google.auth.default()[1]
    name = f"projects/{project}/secrets/mapillary-token/versions/latest"
    return secretmanager.SecretManagerServiceClient().access_secret_version(name=name).payload.data.decode().strip()

def setup(args):
    global CONFIG, AREAS, RUN, BUCKET, DS, TOKEN, TILE_DIR, QC_DIR, catalog
    # 读配置文件（本地路径或 gs:// 都行）；参数变成全局常量 H3_RES、GROUND_M……，和 notebook 里的写法一样
    with fsspec.open(args.config) as f:
        cfg = yaml.safe_load(f)
    for k in CONFIG_KEYS:
        globals()[k.upper()] = cfg[k]
    AREAS = cfg["areas"]
    cfg["dataset_id"] = args.dataset or cfg["dataset_id"]   # 数据集名：--dataset 给了就用它（workflow 传），没给就用配置文件里的
    assert len({a["area_id"] for a in AREAS}) == len(AREAS), "two bboxes in the config share the same area_id"
    CONFIG = json.loads(json.dumps({k: v for k, v in cfg.items() if k != "dataset_id"}))   # 和 manifest.json 里存的格式一样，方便比较

    # 这次跑哪些 bbox：--areas all = 全部；名字写错直接报错
    by_id = {a["area_id"]: a for a in AREAS}
    missing = [x for x in args.areas if x != "all" and x not in by_id]
    assert not missing, f"these area_ids are not in the config: {missing}"
    RUN = AREAS if args.areas == ["all"] else [by_id[x] for x in args.areas]

    BUCKET, DS = args.bucket, f"datasets/{cfg['dataset_id']}"
    TILE_DIR = f"cache/naip_tiles/{GROUND_M}m_{TILE_PX}px"   # 所有数据集共用：同一张 NAIP、同一个格子只裁一次
    QC_DIR = f"qc/{datetime.datetime.now():%Y%m%d-%H%M%S}"    # 检查用的图：本地存一份，再传到 bucket 的 {DS}/qc/ 下
    catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    TOKEN = mapillary_token() if any(a["area_type"] == "city" for a in RUN) else None

    # 数据集已存在：参数必须完全一样；已有的 bbox 不能改，只能在后面加新的（防止两种配置的数据混进同一个数据集）
    rel = f"{DS}/manifest.json"
    if fs.exists(gs(rel)):
        manifest = json.load(fs.open(gs(rel)))
        saved = manifest["config"]
        same = {k: v for k, v in saved.items() if k != "areas"} == {k: v for k, v in CONFIG.items() if k != "areas"}
        changed = [a["area_id"] for a in saved["areas"] if by_id.get(a["area_id"]) != a]
        if not same or changed:
            raise SystemExit(f"Dataset {DS} already exists with a different config "
                             f"({'changed bboxes: ' + str(changed) if changed else 'parameters differ'}). Use a new dataset_id.")
        if len(AREAS) > len(saved["areas"]):              # 加了新 bbox：记进 manifest
            manifest["config"] = CONFIG
            with fs.open(gs(rel), "w") as f:
                json.dump(manifest, f, indent=2)
        print(f"Dataset {DS} exists with the same config: reusing what is already there.")
    else:
        with fs.open(gs(rel), "w") as f:
            json.dump({"dataset_id": cfg["dataset_id"], "created_at": now(), "status": "running", "config": CONFIG}, f, indent=2)
        print(f"Created gs://{BUCKET}/{DS}/")


# ==================== 2. 格子（含去重） ====================
def full_grid(bbox):
    # bbox 里中心点落在框内的全部 H3 格子（约 130 m 宽），不管有没有街景
    lo0, la0, lo1, la1 = bbox
    poly = h3.LatLngPoly([(la0, lo0), (la0, lo1), (la1, lo1), (la1, lo0)])
    ids = sorted(h3.polygon_to_cells(poly, H3_RES))
    return pd.DataFrame([(c, *h3.cell_to_latlng(c)) for c in ids], columns=["h3_index", "lat", "lon"])

def make_cells():
    # 去重：两个 bbox 重叠时，一个格子只归一个 bbox——城市 bbox 优先（有街景），其次配置里排在前面的
    # 先算出配置里全部 bbox 的格子（只是计算，几秒钟），这样只跑一部分 bbox 时也能判断归属
    grids = {a["area_id"]: full_grid(a["bbox"]) for a in AREAS}
    owner = {}
    for a in sorted(AREAS, key=lambda a: a["area_type"] != "city"):       # 稳定排序：城市在前，同类保持配置里的顺序
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
    cells.insert(0, "cell_id", cells.area_id + "_" + cells.h3_index)    # 一看就知道格子属于哪个 bbox
    print(cells.groupby(["area_id", "area_type", "area_subtype"]).size().rename("cells").to_string())
    return cells


# ==================== 3. 航拍图（NAIP） ====================
year_of = lambda it: int(it.properties.get("naip:year", it.datetime.year))
black = lambda img: img.sum(axis=-1) == 0                 # 黑色空白像素
black_frac = lambda img: float(black(img).mean())

def read_png(rel):
    with fs.open(gs(rel), "rb") as f:
        return np.asarray(Image.open(f).convert("RGB"))

def write_png(rel, img):
    # 整张 PNG 在内存里做好才写进 bucket，不会留下写了一半的图
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    with fs.open(gs(rel), "wb") as f:
        f.write(buf.getvalue())

def find_naip(cells):
    # 每个格子找 NAIP_MIN_YEAR 以后、覆盖格子中心的最新一张 NAIP，记下编号、年份、拍摄日期
    # 只查目录，不下载图片；每个 bbox 单独查（bbox 分布在好几个州时，合在一起查会查出大半个美国）
    # 返回：用到的 NAIP {编号: item}，以及每个 bbox 附近的全部 NAIP（补接缝时要用）
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
    # 打开一张 NAIP 一次，以格子中心裁 GROUND_M 见方、缩成 TILE_PX × TILE_PX；读图偶尔失败时等几秒再试（最多 3 次）
    # jobs 每项 (编号, 经度, 纬度, 存放路径)：有路径就存成 PNG、返回空白比例；路径为 None 就直接返回图片（补接缝用）
    out = {}
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"), \
         rasterio.open(planetary_computer.sign(item.assets["image"].href)) as src:
        to_src = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        win = GROUND_M / abs(src.res[0])                  # 航拍图宽度对应多少个原图像素
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
    # 补接缝：格子跨在两张 NAIP 交界上时，只用一张图会有一半是黑的
    # 用相邻的 NAIP（同一年的优先，其次越新越好）把黑的像素填上，直到黑边 ≤ 5%；补好的图存到 mosaic/{H3}.png
    # 这种格子很少，每次都重新补（几分钟）
    bad = cells.loc[area.index][cells.loc[area.index].nodata_frac > MAX_NODATA]
    plan, jobs = {}, {}                                   # plan: 格子 -> 按顺序要用的 NAIP；jobs: NAIP 编号 -> 要从它裁的格子
    for i, r in bad.iterrows():
        sq = box(r.lon - 0.001, r.lat - 0.001, r.lon + 0.001, r.lat + 0.001)          # 约 154 m 见方
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
    # 每个格子裁一张航拍图存进公用缓存；已经裁过的不再裁，只读回来检查空白比例
    cells["tile_path"], cells["nodata_frac"], cells["naip_mosaic"] = None, np.nan, None
    for a in tqdm(RUN, desc="bboxes"):
        aid = a["area_id"]
        if step_status(aid, "naip") == "incomplete":
            continue                                      # 上面查 NAIP 就失败了
        try:
            area = cells[(cells.area_id == aid) & cells.naip_item.notna()]
            n_new = 0
            for item_id, group in area.groupby("naip_item"):
                have = set(fs.find(f"{BUCKET}/{TILE_DIR}/{item_id}"))    # 任何数据集已经裁过的
                jobs = [(i, r.lon, r.lat, f"{TILE_DIR}/{item_id}/{r.h3_index}.png") for i, r in group.iterrows()]
                cached = [j for j in jobs if f"{BUCKET}/{j[3]}" in have]
                todo = [j for j in jobs if f"{BUCKET}/{j[3]}" not in have]
                with ThreadPoolExecutor(16) as ex:        # 已有的：同时读回 16 张，只为算空白比例
                    for (i, _, _, rel), img in zip(cached, ex.map(read_png, [j[3] for j in cached])):
                        cells.loc[i, ["tile_path", "nodata_frac"]] = [rel, black_frac(img)]
                if todo:                                  # 要新裁的：CROP_WORKERS 个线程各自打开一份 NAIP（同一份不能多个线程一起用）
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
            mark(aid, "naip", "incomplete", f"aerial tiles: {why(e)}")   # 已经裁好的图留在缓存里，重跑不会重裁

    # 每个格子写明航拍图的情况：ok / ok_mosaic（补过接缝）/ no_naip_coverage（这里没有 NAIP）/ mostly_black（大部分是空白）
    cells["tile_ok"] = cells.tile_path.notna() & (cells.nodata_frac <= MAX_NODATA)
    cells["tile_reason"] = np.where(cells.tile_ok, np.where(cells.naip_mosaic.notna(), "ok_mosaic", "ok"),
                                    np.where(cells.naip_item.isna(), "no_naip_coverage", "mostly_black"))
    cells.loc[cells.area_id.map(lambda i: step_status(i, "naip") != "complete"), "tile_reason"] = None   # 没做完的 bbox 留空
    print(f"{int(cells.tile_ok.sum())} / {len(cells)} usable aerial tiles")


# ==================== 4. 街景（Mapillary，只有城市 bbox） ====================
R_EARTH = 6378137.0
M_LAT = 110574.0                                          # 每度纬度多少米
META_COLS = ["image_id", "lon", "lat", "captured_at_ms", "compass_raw", "is_pano", "sequence", "quality"]
SV_COLS = ["image_id", "cell_id", "h3_index", "area_id", "lat", "lon", "captured_at", "compass", "compass_raw",
           "heading_conf", "tier", "sequence", "image_path"]

def get(url, params=None, tries=5):
    # 请求失败自动重试，等待越来越长（2、4、8、16 秒）；断网、超时也重试
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
    return r                                              # 重试完还是 429 / 5xx：交给调用的地方报告

# ---- 4a. 照片清单：Mapillary 把地图切成约 2.4 km 的方块，一次请求拿到一块里全部照片的清单（还不下载照片）----
def fetch_tile(t):
    r = get(f"https://tiles.mapillary.com/maps/vtp/mly1_public/2/{t.z}/{t.x}/{t.y}", {"access_token": TOKEN})
    if r.status_code in (204, 404):                       # 这块没有照片
        return b""
    r.raise_for_status()
    return r.content

def decode_tile(content, t):
    # 矢量方块 → 表格；方块坐标（0–4096）换算成经纬度
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
    # bbox 碰到的每个方块：缓存里有就读，没有就下载并存进 cache/mapillary_meta/
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

# ---- 4b. 过滤，分到格子：依次去掉 bbox 外的、太旧的、冬天的、全景图、没有方向的；“漏斗”记下每一步还剩多少张 ----
def filter_photos(raw, bbox):
    min_lon, min_lat, max_lon, max_lat = bbox
    df = raw.copy()
    df[["lat", "lon"]] = df[["lat", "lon"]].astype(float)   # 空的缓存方块把经纬度存成了文字
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

# ---- 4c. 选图规则：位置优先、方向多样（贪心 MMR + 逐级放宽）----
USE_SFM_HEADING = True       # 向 Mapillary 要 SfM 方向：每张照片一次请求，存进缓存，所以只查一次
SFM_DIR = "cache/mapillary_sfm"                    # 每次存一个新文件，多人同时跑不会互相覆盖
SFM_OLD = "cache/mapillary_sfm_heading.parquet"    # 以前用的单个文件，照样读
BETA = 0.15                  # 边缘带 = 中心到边距离的 15%（H3 res 10 约 10 m）
LAMBDA = 0.5                 # 平衡：1 = 只看基础分，0 = 只看多样性
DUP_M, DUP_DEG = 30, 30      # 同一 sequence + 相距 < 30 m + 方向差 < 30° = 近乎复制
TRAVEL_FRAMES = 5            # 行进方向：往前 5 帧 → 往后 5 帧（帧之间只隔约 1 m）
CAND_PER_DIR = 8             # 每个格子按手机罗盘分东南西北 4 组，每组只给最好的 8 张查 SfM 方向、参加选图

ang = lambda a, b: np.abs((np.asarray(a, dtype=float) - b + 180) % 360 - 180)    # 两个方向之间最小的夹角，0–180°
SFM = None                   # 所有存过的 SfM 方向；每次运行读一次，之后随时更新

def sfm_headings(ids):
    # SfM 方向：Mapillary 用多张重叠照片重新算出的方向，比手机罗盘准
    # 返回 (每张照片的 SfM 方向, {没查到的照片: 原因})。Mapillary 明确说“照片不存在”的记为没有 SfM，不算失败
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
                return np.nan                             # 照片已经不存在：没有 SfM 方向（不算失败）
            r.raise_for_status()
            v = r.json().get("computed_compass_angle")
            return np.nan if v is None else float(v)
        except Exception as e:
            return "failed: " + why(e)
    failed = {}
    for k in range(0, len(todo), 2000):                   # 每 2000 张存一次，中途停下重跑能接着查
        chunk = todo[k:k + 2000]
        with ThreadPoolExecutor(max_workers=16) as ex:
            got = list(tqdm(ex.map(ask, chunk), total=len(chunk), desc=f"SfM headings {k + len(chunk)}/{len(todo)}"))
        failed |= {i: v for i, v in zip(chunk, got) if isinstance(v, str)}
        new = pd.DataFrame([(i, v) for i, v in zip(chunk, got) if not isinstance(v, str)], columns=["image_id", "sfm"])
        new.to_parquet(gs(f"{SFM_DIR}/{datetime.datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}.parquet"), index=False)
        SFM = pd.concat([SFM, new], ignore_index=True)    # 失败的不存，下次再查
    return SFM.drop_duplicates("image_id").set_index("image_id").sfm.reindex(ids).to_numpy(dtype=float), failed

def add_travel(df):
    # 行进方向：同一 sequence 里往前数 5 帧 → 往后数 5 帧的方向，移动超过 3 m 才算（要用整条 sequence，所以在挑候选之前算）
    df = df.sort_values(["sequence", "captured_at"]).copy()
    x = df.lon * M_LAT * np.cos(np.radians(df.lat.mean()))
    y = df.lat * M_LAT
    dx = x.groupby(df.sequence).shift(-TRAVEL_FRAMES) - x.groupby(df.sequence).shift(TRAVEL_FRAMES)
    dy = y.groupby(df.sequence).shift(-TRAVEL_FRAMES) - y.groupby(df.sequence).shift(TRAVEL_FRAMES)
    df["travel"] = pd.Series(np.degrees(np.arctan2(dx, dy)) % 360, index=df.index).where(np.hypot(dx, dy) > 3)
    return df

def shortlist(df, naip_of):
    # 先挑候选：不看方向，按“离中心近 0.65 + 画质 0.15 + 离 NAIP 日期近 0.2”排序，每个方向只留前 CAND_PER_DIR 张（查方向的请求少很多）
    centre = {h: h3.cell_to_latlng(h) for h in df.h3_index.unique()}
    clat = df.h3_index.map(lambda h: centre[h][0]).astype(float)
    clon = df.h3_index.map(lambda h: centre[h][1]).astype(float)
    d = np.hypot((df.lon - clon) * M_LAT * np.cos(np.radians(clat)), (df.lat - clat) * M_LAT)
    years = ((df.captured_at - pd.to_datetime(df.h3_index.map(naip_of), utc=True)).abs().dt.days / 365.25).fillna(4)
    pre = 0.65 * (1 - d / 76) + 0.15 * pd.to_numeric(df.quality, errors="coerce").fillna(0.5) + 0.2 * (1 - np.minimum(years / 4, 1))
    return (df.assign(_pre=pre, _dir=(df.compass // 90).astype(int)).sort_values("_pre", ascending=False)
              .groupby(["h3_index", "_dir"]).head(CAND_PER_DIR).drop(columns=["_pre", "_dir"]))

def add_heading(df):
    # 方向可信度：每张照片得到最可信的方向 compass 和可信度 heading_conf（原始罗盘留在 compass_raw）
    df = df.copy()
    df["compass_raw"] = df.compass
    sfm, failed = sfm_headings(df.image_id.tolist()) if USE_SFM_HEADING else (np.full(len(df), np.nan), {})
    has_sfm = ~np.isnan(sfm)
    agree = ang(df.compass_raw, df.travel) < 30           # 罗盘和行进方向一致 = 朝前拍，可信
    # 优先级：SfM 角度(1.0) > 和行进方向一致的罗盘(0.7) > 行进方向(0.4) > 原始罗盘(0.4)
    df["compass"] = np.select([has_sfm, agree, df.travel.notna()], [sfm % 360, df.compass_raw, df.travel], df.compass_raw)
    df["heading_conf"] = np.select([has_sfm, agree], [1.0, 0.7], 0.4)
    return df, failed

def pick_by_position(g, k, naip_date):
    cell = g.h3_index.iloc[0]
    clat, clon = h3.cell_to_latlng(cell)
    to_xy = lambda la, lo: np.c_[(np.asarray(lo) - clon) * M_LAT * np.cos(np.radians(clat)), (np.asarray(la) - clat) * M_LAT]
    hexagon = Polygon(to_xy(*zip(*h3.cell_to_boundary(cell))))       # 单位米，格子中心在 (0, 0)
    r_c = hexagon.exterior.distance(Point(0, 0))                     # 中心 → 边，约 66 m
    R_c = np.hypot(*hexagon.exterior.coords[0])                      # 中心 → 角，约 76 m
    P = to_xy(g.lat, g.lon)
    g = g.assign(x=P[:, 0], y=P[:, 1], d=np.hypot(P[:, 0], P[:, 1]),
                 core=[hexagon.exterior.distance(Point(p)) >= BETA * r_c for p in P],   # 中心区 = 不在边缘带
                 out=ang(g.compass, np.degrees(np.arctan2(-P[:, 0], -P[:, 1])) % 360) > 90)  # 背对格子中心

    # 硬性规则 H1：在边缘带、且方向可信地朝外 = 拍的是隔壁格子，永远不选
    g = g[~(~g.core & g.out & (g.heading_conf >= 0.7))]

    # 第 1 步 基础分：离中心越近越好（主要），其次是画质、离 NAIP 拍摄日近；边缘且方向不确定再扣一点
    #   时间分：和 NAIP 同一天 = 1，差 2 年 = 0.5，差 4 年及以上 = 0（格子没有 NAIP 时都按 0 算）
    years = (g.captured_at - pd.Timestamp(naip_date, tz="UTC")).abs().dt.days / 365.25 if isinstance(naip_date, str) else 4
    base = (0.65 * (1 - g.d / R_c) + 0.15 * pd.to_numeric(g.quality, errors="coerce").fillna(0.5)
            + 0.2 * (1 - np.minimum(years / 4, 1)) - 0.2 * ~g.core * (1 - g.heading_conf)).to_numpy()

    X, Y, H, C, S, core = (g[c].to_numpy() for c in ["x", "y", "compass", "heading_conf", "sequence", "core"])
    def sim(j):
        # 和已选照片 j 有多像：方向 0.6 + 同一 sequence 0.3 + 位置 0.1。方向不可信时按“同方向”算，不白拿多样性分
        return (0.6 * (1 - np.minimum(C, C[j]) * ang(H, H[j]) / 180) + 0.3 * (S == S[j])
                + 0.1 * np.clip(1 - np.hypot(X - X[j], Y - Y[j]) / 60, 0, None))
    def near_copy(j):
        # 硬性规则 H2：同一 sequence、相距 < 30 m、方向差 < 30° = 近乎复制，不能和 j 同时选
        return (S == S[j]) & (np.hypot(X - X[j], Y - Y[j]) < DUP_M) & (ang(H, H[j]) < DUP_DEG)

    # 第 3 步 逐级放宽：候选池从严到松，A 不够才开 B，B 不够才开 C；已选的照片保留
    #   A：只用中心区，每个 sequence 最多 1 张                       → 没风险
    #   B：仍只用中心区，同一 sequence 可以再选（但不能近乎复制）       → 只是独立性下降
    #   C：放开边缘带（朝内或方向不确定的照片）                       → 可能拍到隔壁，所以最后才开
    #   D：选完还不到 MIN_IMAGES_PER_CELL 张 → 这个格子不要街景（格子本身保留，只有航拍图），不硬凑
    chosen, tiers = [], []
    blocked, max_sim = np.zeros(len(g), bool), np.zeros(len(g))
    for tier, pool in [("A", core), ("B", core), ("C", np.ones(len(g), bool))]:
        while len(chosen) < k:
            ok = pool & ~blocked
            if tier == "A":
                ok &= ~np.isin(S, S[chosen])
            if not ok.any():
                break
            # 第 2 步 MMR：选“基础分高、又和已选照片中最像的那张差得最远”的照片
            j = int(np.argmax(np.where(ok, LAMBDA * base - (1 - LAMBDA) * max_sim, -np.inf)))
            chosen.append(j); tiers.append(tier)
            blocked |= near_copy(j)                       # 也会挡住 j 自己
            max_sim = np.maximum(max_sim, sim(j))
    return g.iloc[chosen].assign(tier=tiers).drop(columns=["x", "y", "d", "core", "out"])

# ---- 4d. 选图 → 下载照片 → 存盘 ----
def download_image(image_id):
    # 结果三种："ok" 下好了（或缓存里已有）；"deleted" Mapillary 明确说照片不存在（数据本身没有，补选下一张）；
    # "failed: 原因" 网络 / 限流等暂时问题（这个 bbox 标成未完成，重跑时只补下这些）
    # 整张图先下载到内存、确认能完整打开，才存进 bucket，所以不会留下空文件或错误页面
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
            return "deleted"                              # 这张照片没有可下载的版本
        img = get(url)                                    # 图片链接会过期，所以每次都重新要
        img.raise_for_status()
        Image.open(io.BytesIO(img.content)).load()        # 完整解码一遍：错误页面或下了一半的文件会在这里失败
        with fs.open(gs(path), "wb") as f:
            f.write(img.content)
        return "ok"
    except Exception as e:
        return "failed: " + why(e)

def street_view(cells):
    # 每个城市 bbox：照片清单 → 过滤 → 挑候选 → 查方向 → 选图 → 下载 → 存 streetview/{area_id}.parquet
    # 一个 bbox 做完立即存盘；已经做完的直接读回，不再选图和下载
    sv_list, funnels, street_reason = [], {}, {}          # street_reason: cell_id -> 这个格子为什么有 / 没有街景
    for a in RUN:
        aid = a["area_id"]
        area = cells[cells.area_id == aid]
        if a["area_type"] != "city":                      # 郊区不需要街景
            mark(aid, "street_view", "not needed")
            street_reason |= dict.fromkeys(area.cell_id, "suburb")
            continue
        if step_status(aid, "naip") != "complete":        # 选图要用 NAIP 日期，航拍图没做完就先不做
            mark(aid, "street_view", "incomplete", "waiting: aerial tiles are not complete for this bbox")
            continue
        rel, old_cells = f"{DS}/streetview/{aid}.parquet", f"{DS}/cells/{aid}.parquet"
        try:
            if fs.exists(gs(rel)) and fs.exists(gs(old_cells)):      # 之前做完了：读回照片和原因
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
            df = df[df.h3_index.isin(area.h3_index)]      # 只要这个 bbox 的格子（去重后归别的 bbox 的格子也不要）
            naip_of = area.set_index("h3_index").naip_date
            df, sfm_failed = add_heading(shortlist(add_travel(df), naip_of))
            if sfm_failed:                                # 有照片方向没查到（网络）：不选图、不定稿
                raise Incomplete(f"{len(sfm_failed)} photo headings could not be fetched, e.g. {next(iter(sfm_failed.values()))}")

            # 选图 → 下载。Mapillary 说已经不存在的照片，去掉后给那个格子重新选（最多 3 轮）
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

            # 有照片没下载成功（网络）：这个 bbox 不存，记下是哪些照片、哪些格子
            failed = [dict(image_id=i, cell_id=f"{aid}_{h}", error=result[i])
                      for i, h in zip(p.image_id, p.h3_index) if result[i].startswith("failed")]
            if failed:
                raise Incomplete(f"{len(failed)} photos failed to download ({len({f['cell_id'] for f in failed})} cells), "
                                 f"e.g. {failed[0]['error']}", failed)

            p = p[p.image_id.map(result) == "ok"]
            p = p.assign(area_id=aid, cell_id=aid + "_" + p.h3_index.astype(str), image_path="cache/mapillary_images/" + p.image_id.astype(str) + ".jpg")
            p = p[p.groupby("cell_id").image_id.transform("size") >= MIN_IMAGES_PER_CELL]   # 照片太少：这个格子不要街景
            p["captured_at"] = p.captured_at.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            p = p[SV_COLS].sort_values(["cell_id", "image_id"]).reset_index(drop=True)
            p.to_parquet(gs(rel), index=False)
            sv_list.append(p)

            # 每个格子写明街景情况：has_photos / no_photos（筛选后一张都没有）/ too_few_photos（不够 MIN_IMAGES_PER_CELL 张）/ photos_deleted（照片被作者删了，补选后还是不够）
            has, lost = set(p.cell_id), set(df.h3_index[df.image_id.isin(gone)])
            for c, h in zip(area.cell_id, area.h3_index):
                street_reason[c] = ("has_photos" if c in has else "photos_deleted" if h in lost
                                    else "too_few_photos" if h in groups else "no_photos")
            mark(aid, "street_view", "complete", photos=len(p), photos_deleted_on_mapillary=len(gone))
            print(f"  {p.cell_id.nunique()} of {len(area)} cells have street photos ({len(p)} photos)")
        except Exception as e:                            # 这个 bbox 出错：标成未完成（失败照片的明细存进状态文件），继续下一个
            mark(aid, "street_view", "incomplete", why(e), **({"failed": e.args[1]} if isinstance(e, Incomplete) and len(e.args) > 1 else {}))

    sv = pd.concat(sv_list, ignore_index=True) if sv_list else pd.DataFrame(columns=SV_COLS)
    cells["street_reason"] = cells.cell_id.map(street_reason)   # 没做完的城市 bbox 留空
    print(f"\nStreet photos: {len(sv)} in {sv.cell_id.nunique()} cells")
    return sv, funnels


# ==================== 5. 地图特征（OpenStreetMap + WorldCover） ====================
# 每个格子和航拍图同一块 GROUND_M 见方：各级道路长度、自行车道、路口数、POI 数、水 / 绿地 / 建筑占比、WorldCover 各类土地占比
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
    # 返回整州 OSM 地图的本地副本（pyrosm 只能读本地文件）；正本存在 bucket 的 cache/osm/
    # 先写成 .part，完整了再改名：中途停下不会留下半个文件
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
    # 用 osmium 把整州地图剪成格子附近一小块（几秒），pyrosm 就不用把整州文件扫 5 遍；没装 osmium 就读整个文件（慢，结果一样）
    if not shutil.which("osmium"):
        return pbf
    out = f"{pbf[:-len('.osm.pbf')]}_{w:.3f}_{s:.3f}_{e:.3f}_{n:.3f}.osm.pbf"
    if not os.path.exists(out):
        subprocess.run(["osmium", "extract", "-b", f"{w},{s},{e},{n}", pbf, "-o", out + ".part", "-f", "pbf", "--overwrite"],
                       check=True, capture_output=True)
        os.replace(out + ".part", out)
    return out

def area_frac(layer, fp):
    # 一层多边形盖住了方块的多少比例（重叠的只算一次）
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

    # 道路：在每个路口断开，才能数路口
    nodes, edges = osm.get_network(network_type="all", nodes=True,
                                   extra_attributes=["cycleway", "cycleway:left", "cycleway:right", "cycleway:both"])
    edges = edges.to_crs(utm).reset_index(drop=True)
    edges["rclass"] = edges.highway.map(HIGHWAY_CLASS).fillna("other")
    edges["bike"] = False
    for c in [c for c in edges.columns if c.startswith("cycleway")]:
        edges["bike"] |= edges[c].notna() & ~edges[c].isin(NO_BIKE_LANE)
    degree = pd.concat([edges.u, edges.v]).value_counts()           # 每个节点连着几段路
    nodes = nodes.to_crs(utm)
    junctions = nodes[nodes.id.map(degree).fillna(0) >= 3].reset_index(drop=True)

    def layer(tags):
        g = osm.get_data_by_custom_criteria(custom_filter=tags, filter_type="keep", keep_nodes=False, keep_ways=True, keep_relations=True)
        if g is None or len(g) == 0:
            return gpd.GeoDataFrame(geometry=[], crs=utm)
        g = g[["geometry"]].reset_index(drop=True).to_crs(utm)
        g["geometry"] = g.geometry.make_valid()          # 修复 OSM 里自相交等坏掉的多边形，否则算面积会报 TopologyException
        return g
    water = layer({"natural": ["water", "wetland"], "waterway": ["riverbank", "dock"], "landuse": ["reservoir", "basin"]})
    green = layer({"leisure": ["park", "garden", "playground", "recreation_ground", "nature_reserve", "golf_course"],
                   "landuse": ["grass", "meadow", "forest", "village_green", "recreation_ground", "cemetery"],
                   "natural": ["wood", "scrub", "grassland"]})
    buildings = layer({"building": True})
    pois = osm.get_pois(custom_filter={"amenity": True, "shop": True, "tourism": True})
    pois = pois[["geometry"]].to_crs(utm).reset_index(drop=True) if pois is not None and len(pois) else gpd.GeoDataFrame(geometry=[], crs=utm)
    pois["geometry"] = pois.geometry.representative_point()         # 面状 POI（比如商场）只算一次
    print(f"  OSM: {len(edges):,} road segments, {len(junctions):,} intersections, {len(buildings):,} buildings, {len(pois):,} POIs")

    rows = []
    for cid, fp in tqdm(list(zip(area_cells.cell_id, foot)), desc="cells"):
        near = edges.iloc[edges.sindex.query(fp, predicate="intersects")]
        length = near.geometry.intersection(fp).length               # 每条路在方块里的长度（米）
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

    # WorldCover：方块里 10 m 土地覆盖像素，各类别占比
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
                valid = sum(counts[c] for c in WC)                    # 不算 NoData（类别 0）
                wc_rows[cid] = {"cell_id": cid, **{f"wc_{name}_frac": (float(counts[c] / valid) if valid else np.nan) for c, name in WC.items()}}
    wc = pd.DataFrame(list(wc_rows.values()), columns=["cell_id", *[f"wc_{n}_frac" for n in WC.values()]])
    return feat.merge(wc, on="cell_id", how="left")

def url_exists(url):
    # 问 Geofabrik 这个文件在不在：在 = True；明确不在 = False；重试 4 次都没回应 = None（不报错，交给下面换年份）
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
    # 选用哪一年的 OSM 快照（Geofabrik 每年 1 月 1 日给每个州存一份）：
    # 1) bucket 里已经有、和 NAIP 拍摄日相差 2 年以内的 → 直接用其中离 NAIP 最近的那份，不联网
    # 2) 没有 → 去 Geofabrik 按“离 NAIP 最近 → 更老 → 更新”的顺序找，第一个确认存在的就用
    # 3) 都不行 → 返回 None：这个 bbox 标成未完成
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
    # Geofabrik 的区域目录（各州边界 + 下载地址），用来判断 bbox 在哪个州；bucket 里有就直接用，没有才下载一次
    idx = "cache/osm/geofabrik_index.json"
    if not fs.exists(gs(idx)):
        with fs.open(gs(idx), "w") as f:
            f.write(requests.get("https://download.geofabrik.de/index-v1.json", timeout=120).text)
    regions = [(shape(f["geometry"]), f["properties"]["urls"]["pbf"]) for f in json.load(fs.open(gs(idx)))["features"]
               if "/north-america/us/" in f["properties"]["urls"].get("pbf", "")]

    # 每个 bbox 一个文件；之前算完的直接读回，不重算
    feat_list = []
    cells["osm_url"] = None
    for a in RUN:
        aid = a["area_id"]
        area = cells[cells.area_id == aid]
        rel = f"{DS}/osm_features/{aid}.parquet"
        if step_status(aid, "naip") != "complete":        # 选快照要用 NAIP 日期，航拍图没做完就先不做
            mark(aid, "osm", "incomplete", "waiting: aerial tiles are not complete for this bbox")
            continue
        try:
            # bbox 中心点所在的最小区域（一般是州，加州细分到南加 / 北加）
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
                for url, group in cells.loc[area.index].groupby("osm_url"):     # 一般一个 bbox 只用一份快照
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


# ==================== 6. 保存 + 汇总 ====================
def read_all(sub):
    # 读 bucket 里全部 bbox 的文件（谁跑的都算），同时读 16 个
    with ThreadPoolExecutor(16) as ex:
        parts = list(ex.map(lambda p: pd.read_parquet(f"gs://{p}"), fs.find(f"{BUCKET}/{DS}/{sub}")))
    return pd.concat(parts, ignore_index=True) if parts else None

def save(cells, sv, funnels):
    # 每个 bbox 一个 cells 文件；只存航拍图那一步做完的 bbox（格子表里的航拍图信息要完整）
    cells["updated_at"] = now()
    for area_id, part in cells.groupby("area_id"):
        if step_status(area_id, "naip") == "complete":
            part.to_parquet(gs(f"{DS}/cells/{area_id}.parquet"), index=False)

    # 每个 bbox 的状态存进 status/{area_id}.json：之前的记录保留，这次跑过的步骤用新结果覆盖
    for a in RUN:
        srel = f"{DS}/status/{a['area_id']}.json"
        old = json.load(fs.open(gs(srel))) if fs.exists(gs(srel)) else {"steps": {}}
        with fs.open(gs(srel), "w") as f:
            json.dump({"area_id": a["area_id"], "updated_at": now(), "steps": {**old["steps"], **STEPS.get(a["area_id"], {})}},
                      f, indent=2, ensure_ascii=False, default=str)

    # 汇总整个数据集（读 bucket 里全部 bbox，不只是这次跑的，所以谁最后运行，汇总都是完整的）
    fs.invalidate_cache()                                 # 清掉文件列表的缓存，刚写的文件才列得出来
    cells_all = read_all("cells")
    cells_all = cells.iloc[:0] if cells_all is None else cells_all
    sv_all = read_all("streetview")
    sv_all = sv.iloc[:0] if sv_all is None else sv_all
    with ThreadPoolExecutor(16) as ex:
        status_all = {s["area_id"]: s["steps"] for s in ex.map(lambda p: json.load(fs.open(p)), fs.find(f"{BUCKET}/{DS}/status"))}
    def bbox_state(area_id):
        # complete / incomplete（附原因）/ not run yet
        steps = status_all.get(area_id)
        if steps is None:
            return "not run yet", ""
        bad = {k: v.get("reason", "") for k, v in steps.items() if v["status"] == "incomplete"}
        return ("incomplete", "; ".join(f"{k}: {r}" for k, r in bad.items())) if bad else ("complete", "")

    # areas.parquet：配置里每个 bbox 一行——状态、格子数、航拍图数、街景数、各种“本来就没有”的格子数、用的 NAIP 日期和 OSM
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

    # manifest.json：进度、总数、数据来源
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

    # 未完成清单：网络等问题没做完的 bbox，附原因和可以直接复制的重跑参数
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


# ==================== 7. 检查用的图（存到本地 qc/ 和 bucket 的 {DS}/qc/） ====================
def thumb(path, px):
    # 小缩略图塞进网页（bucket 是私有的，浏览器不能直接读图）
    with fs.open(gs(path), "rb") as fh:
        im = Image.open(fh).convert("RGB")
    im.thumbnail((px, px)); buf = io.BytesIO(); im.save(buf, "JPEG", quality=62)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

def save_fig(name):
    plt.savefig(f"{QC_DIR}/{name}", dpi=100, bbox_inches="tight")
    plt.close()

def qc(cells, sv):
    os.makedirs(QC_DIR, exist_ok=True)

    # tiles.png：每个 bbox 看 4 张航拍图
    show = cells[cells.tile_ok].groupby("area_id").head(4)
    rows = max(1, -(-len(show) // 4))
    fig, axes = plt.subplots(rows, 4, figsize=(12, 3.1 * rows), squeeze=False)
    for ax in axes.ravel(): ax.axis("off")
    for ax, r in zip(axes.ravel(), show.itertuples()):
        ax.imshow(read_png(r.tile_path))
        ax.set_title(f"{r.area_id} · NAIP {r.naip_date}", fontsize=8)
    save_fig("tiles.png")

    # areas.png：每个 bbox 一张小图，格子按状态上色——有街景 / 城市里没有街景 / 郊区
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
        # photos.png：随机看两个有街景的格子的照片
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
    # map.html 交互地图：底图 + bbox 边框 + 全部格子（填色 = 有街景）+ 过滤后的照片点（颜色 = 年份）+ 选中照片的拍摄方向
    # 右上角开关图层；大 bbox 只画一部分照片点，文件才不会大到浏览器打不开
    ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
    ARROW_M = 30                 # 箭头长度（米）
    MAP_MAX_POINTS = 4000        # 每个 bbox 最多画多少个“过滤后的照片点”（只影响显示）
    SAMPLE_GRID = 3              # 抽样看图：每个城市 bbox 切成 3×3 块，每块随机挑 1 个有街景的格子（最多 9 个）
    SAMPLE_SEED = 0              # 想换一批样本就改这个数
    corners = lambda b: [[b[1], b[0]], [b[3], b[2]]]
    rnd = lambda v: round(float(v), 6)

    # 过滤后的照片（从缓存的方块重新读，不下载新东西）；大 bbox 随机抽一部分
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

    # 每张选中照片朝哪边：和指向格子中心的方向比
    lat0 = sv.lat.mean()
    m_lat, m_lon = 110574.0, 111320.0 * np.cos(np.radians(lat0))
    centre = {h: h3.cell_to_latlng(h) for h in sv.h3_index.unique()}
    dx = (sv.h3_index.map(lambda h: centre[h][1]) - sv.lon) * m_lon
    dy = (sv.h3_index.map(lambda h: centre[h][0]) - sv.lat) * m_lat
    off = np.abs((sv.compass - np.degrees(np.arctan2(dx, dy)) % 360 + 180) % 360 - 180)
    away = (off > 90) & (np.hypot(dx, dy) >= 15)          # 离中心 15 m 以内，朝哪都算朝里
    print(f"{int(away.sum())} of {len(sv)} selected photos ({away.mean():.0%}) point away from their cell centre "
          f"(random directions would give about 50%)")

    m = folium.Map(tiles=None, prefer_canvas=True)        # canvas 画一万多个图形也不卡
    folium.TileLayer(ESRI.format("World_Imagery"), attr="Esri", name="Basemap: satellite", overlay=True, show=True).add_to(m)
    folium.TileLayer(ESRI.format("World_Street_Map"), attr="Esri", name="Basemap: street map", overlay=True, show=False).add_to(m)

    # 格子（最底层）：没街景的格子透明但仍“有填充”，鼠标停在格子里任何地方都能看到提示
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

    # bbox 边框用线画（用矩形会挡住里面格子的鼠标提示）
    area_layer = folium.FeatureGroup(name="Areas (red = city, blue = suburb)").add_to(m)
    for a in RUN:
        lo0, la0, lo1, la1 = a["bbox"]
        folium.PolyLine([(la0, lo0), (la0, lo1), (la1, lo1), (la1, lo0), (la0, lo0)], color="#d1342a" if a["area_type"] == "city" else "#2f6f9f",
                        weight=2, tooltip=f"{a['area_id']} · {a['area_subtype']}").add_to(area_layer)

    # 过滤后的照片：一层点，颜色 = 年份
    points = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [rnd(lo), rnd(la)]}, "properties": {"year": int(y)}}
        for lo, la, y in zip(kept.lon, kept.lat, kept.captured_at.dt.year)]}
    folium.GeoJson(points, name=f"Photos after filtering ({len(kept):,} shown of {n_all:,}; color = year)",
                   marker=folium.CircleMarker(radius=2, weight=0, fill=True, fill_opacity=0.7),
                   style_function=lambda f: {"fillColor": year_color[f["properties"]["year"]]}).add_to(m)

    # 选中的照片：箭头 + 带提示的点（绿 = 朝格子里面，红 = 朝外）
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

    # 抽样格子：每个城市 bbox 切成 3×3 块，每块随机挑 1 个有街景的格子；点开弹出它的航拍图和选中的街景
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

    # 左上角下拉框：选一个 bbox，视野直接跳过去
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


# ==================== 入口 ====================
def main():
    load_dotenv(find_dotenv(usecwd=True))                 # 本地读 .env；容器里用 --env-file 传，没有 .env 也没关系
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
