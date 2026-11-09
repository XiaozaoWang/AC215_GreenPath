"""
GreenPath 打分（原 02 notebook）：用 Gemini 给 data-collector 做好的数据集打分。
每张图 16 个维度、每个 1–5 分。航拍图：所有格子都打；街景：只打有街景的格子，再按格子取平均。
格子的分数就是模型要学的标签 Y。

用法：
  python cli.py --dataset 1026_Full_data --areas city_ma_boston_big sub_ma_hadley_farm
  python cli.py --dataset 1026_Full_data --areas all --no-qc
  python cli.py --dataset 1026_Full_data --areas all --max-images 5     # 每个 bbox 只新打 5 张，先看看分数和费用

打过的图不会重打、不会重复收费，断了重跑会接着打。结果存在 datasets/{数据集}/labels/ 下：
  aerial_{规则}_{模型}/   航拍图：tile_labels/（每张图的原始分）、cell_labels/（每个格子 0–1 的分）
  street_{规则}_{模型}/   街景：  image_labels/（每张照片的原始分）、cell_labels/（每个格子的平均分）
换规则或换模型会存进新文件夹，旧结果不动。
"""
import argparse, base64, datetime, hashlib, io, json, os, re, textwrap, threading, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import gcsfs, google.auth, h3
import numpy as np, pandas as pd
import pyarrow.parquet as pq
import folium
import matplotlib
matplotlib.use("Agg")                                     # 没有屏幕：图只存成文件
import matplotlib.pyplot as plt
from matplotlib.colors import to_hex
from dotenv import load_dotenv, find_dotenv
from google import genai
from google.genai import types, errors as genai_errors
from PIL import Image
from pydantic import BaseModel, create_model
from tqdm import tqdm

# 16 个维度：只在这里列一次，必须和两个规则文件（prompt）里写的名字完全一样（下面会检查）
DIMS = ["tree_canopy", "forest_environment", "grassland", "open_landscape", "farmland_pastoral", "water_access", "hilly_terrain",
        "historic_character", "architectural_character", "street_enclosure", "urban_density", "commercial_district",
        "road_width", "industrial_area", "visual_clutter", "scenic_appeal"]
THINKING_BUDGET = 0                                       # 0 = 不要额外的“思考” token（省钱）
MIN_USABLE_IMAGES = 2                                     # 一个格子至少要有几张能用的街景，才算有街景标签
PRICE_IN_PER_M, PRICE_OUT_PER_M = 0.30, 2.50              # 每百万 token 的价格（美元），只用来估算费用

fs = gcsfs.GCSFileSystem()
gs = lambda rel: f"gs://{BUCKET}/{rel}"                   # bucket 里的相对路径 → 完整 gs:// 路径
now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
cost_usd = lambda tin, tout: tin / 1e6 * PRICE_IN_PER_M + tout / 1e6 * PRICE_OUT_PER_M


# ==================== 状态记录 ====================
# 每个 bbox 的航拍图 / 街景只有两种结果：complete（全部打完）/ incomplete（限流、服务器忙、网络等暂时问题没打完，要重跑）
# 图本身的问题（Gemini 判定不能用、图片坏了）不算 incomplete，写在每张图的 unusable_reason 里
STEPS = {}                                                # {area_id: {"aerial" / "street": {"status": ..., "reason": ...}}}

def mark(area_id, step, status, reason=""):
    STEPS.setdefault(area_id, {})[step] = {"status": status, **({"reason": reason} if reason else {})}
    if status == "incomplete":
        print(f"!!! {area_id}: {step} NOT complete: {reason}")

def step_status(area_id, step):
    return STEPS.get(area_id, {}).get(step, {}).get("status")

def why(err):
    # 把一张图的报错翻译成一句原因；其他情况给出原始报错（多半是代码或文件问题）
    if "429" in err or "RESOURCE_EXHAUSTED" in err:
        return "rate limited (HTTP 429): lower --workers"
    if re.search(r"\b50\d\b|UNAVAILABLE|INTERNAL", err):
        return "Gemini server error (HTTP 5xx)"
    if re.search(r"(?i)timeout|timed out|connection|ssl", err):
        return "network error"
    return f"error: {err[:150]}"

def check_done(area_id, step, want, got, errors):
    # want = 这个 bbox 要打分的图，got = 已经打了分的图（含以前打的）。全打完 → complete；否则 → incomplete + 原因（同一种原因合并计数）
    left = int((~want.isin(got)).sum())
    if not left:
        mark(area_id, step, "complete")
        return True
    reason = "; ".join(f"{r} ×{n}" for r, n in Counter(why(e) for e in errors.values()).most_common()) \
             or f"--max-images {MAX_IMAGES}: not all scored yet"
    mark(area_id, step, "incomplete", f"{left} of {len(want)} images not scored: {reason}")
    return False

# 计时：每个大步骤结束时打印这一步和总共用了几分钟
T0 = T_LAST = time.time()
TIMES = {}
def tick(step):
    global T_LAST
    TIMES[step] = (time.time() - T_LAST) / 60
    T_LAST = time.time()
    print(f"⏱ {step}: {TIMES[step]:.1f} min (total {(T_LAST - T0) / 60:.1f} min)")


# ==================== 1. 读数据集 ====================
def area_files(folder):
    # 一个文件夹里有哪些 bbox 的文件：{area_id: 路径}
    return {p.rsplit("/", 1)[-1][:-len(".parquet")]: p for p in fs.find(f"{BUCKET}/{DS}/{folder}")}

def read_areas(folder, ids):
    # 每个 bbox 一个文件 → 合成一张表，同时读 16 个；没有文件的 bbox 跳过
    have = area_files(folder)
    with ThreadPoolExecutor(16) as ex:
        parts = list(ex.map(lambda p: pd.read_parquet(f"gs://{p}"), [have[i] for i in ids if i in have]))
    return pd.concat(parts, ignore_index=True) if parts else None

def row_counts(folder):
    # 一个文件夹里每个 bbox 的文件有几行（只读文件尾部的信息，很快）
    have = area_files(folder)
    with ThreadPoolExecutor(16) as ex:
        return dict(zip(have, ex.map(lambda p: pq.ParquetFile(fs.open(p)).metadata.num_rows, have.values())))

def progress():
    # 进度表：每行一个 bbox，数的是 bucket 里实际有的文件（谁跑的都算）
    aer, st = row_counts(f"labels/{AERIAL_ID}/tile_labels"), row_counts(f"labels/{STREET_ID}/image_labels")
    return pd.DataFrame([dict(area_id=a["area_id"], type=a["area_type"],
                              cells=int((cells.area_id == a["area_id"]).sum()),
                              tiles_ok=int(cells.tile_ok[cells.area_id == a["area_id"]].sum()),
                              aerial_scored=aer.get(a["area_id"], 0),
                              street_photos=int((sv.area_id == a["area_id"]).sum()),
                              street_scored=st.get(a["area_id"], 0))
                         for a in RUN]).set_index("area_id")

def setup(args):
    global BUCKET, DS, MODEL, MAX_IMAGES, WORKERS, AERIAL_PROMPT, STREET_PROMPT, AERIAL_ID, STREET_ID, RUN, QC_DIR, LOCATION, cells, sv, client
    BUCKET, DS, MODEL = args.bucket, f"datasets/{args.dataset}", args.model
    MAX_IMAGES, WORKERS = args.max_images, args.workers
    AERIAL_PROMPT, STREET_PROMPT = args.aerial_prompt, args.street_prompt
    AERIAL_ID, STREET_ID = f"aerial_{AERIAL_PROMPT}_{MODEL}", f"street_{STREET_PROMPT}_{MODEL}"   # 标签文件夹名 = 图的种类 + 规则版本 + 模型
    QC_DIR = f"qc/labels-{datetime.datetime.now():%Y%m%d-%H%M%S}"   # 检查用的图：本地存一份，再传到 bucket 的 {DS}/qc/ 下

    # 这次给哪些 bbox 打分：bbox 清单来自 data-collector 写的 manifest.json；采集还没做完的 bbox 跳过
    areas = json.load(fs.open(gs(f"{DS}/manifest.json")))["config"]["areas"]
    by_id = {a["area_id"]: a for a in areas}
    missing = [x for x in args.areas if x != "all" and x not in by_id]
    assert not missing, f"these area_ids are not in the dataset: {missing}"
    RUN = areas if args.areas == ["all"] else [by_id[x] for x in args.areas]
    cells = read_areas("cells", [a["area_id"] for a in RUN])
    assert cells is not None, "no cells yet: run data-collector for these bboxes first"
    skipped = [a["area_id"] for a in RUN if a["area_id"] not in set(cells.area_id)]
    if skipped:
        print("not processed by data-collector yet, skipped:", skipped)
    RUN = [a for a in RUN if a["area_id"] in set(cells.area_id)]
    sv = read_areas("streetview", [a["area_id"] for a in RUN])
    if sv is None:
        sv = pd.DataFrame(columns=["image_id", "cell_id", "h3_index", "area_id", "compass", "captured_at", "lat", "lon", "image_path"])

    # 连接 Gemini（Vertex AI）：用当前身份——本地是你的 gcloud 登录，云上是任务的服务账号；不需要 API key
    # 项目：读环境变量 GOOGLE_CLOUD_PROJECT（group project，能用 gemini-2.5-flash）；本地来自 .env，云上由流程传入
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or google.auth.default()[1]
    LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    client = genai.Client(vertexai=True, project=project, location=LOCATION)
    print(f"Dataset {args.dataset}: {len(RUN)} bboxes, {len(cells)} cells, {len(sv)} street photos | {MODEL} in {project} / {LOCATION}")
    print(progress().to_string())


# ==================== 2. 读打分规则（prompt） ====================
def load_prompt(version, label_dir):
    # 先找本地 prompts/{版本}.txt（第一次用就传到 bucket 的 prompts/ 共享）；本地没有就读 bucket 里的
    local, shared = os.path.join("prompts", f"{version}.txt"), f"prompts/{version}.txt"
    if os.path.exists(local):
        text = open(local, encoding="utf-8").read()
        if not fs.exists(gs(shared)):
            with fs.open(gs(shared), "wb") as f:
                f.write(text.encode("utf-8"))
        assert fs.open(gs(shared), "rb").read().decode("utf-8") == text, \
            f"{local} differs from gs://{BUCKET}/{shared}. Save your changes as a new version."
    else:
        assert fs.exists(gs(shared)), f"gs://{BUCKET}/{shared} not found: upload {version}.txt to the bucket's prompts/ folder first"
        text = fs.open(gs(shared), "rb").read().decode("utf-8")
    # 规则里写的维度（以“维度名 - ”开头的行）必须和 DIMS 完全一样
    found = set(re.findall(r"^([a-z_]+) - ", text, flags=re.M))
    assert found == set(DIMS), f"{shared} and DIMS differ. Only in DIMS: {set(DIMS) - found}. Only in the prompt: {found - set(DIMS)}"
    # 标签文件夹里存一份规则副本；已有的副本和现在的规则不一样 = 规则改了却没换版本号，停下
    copy_rel = f"{label_dir}/prompt.txt"
    if fs.exists(gs(copy_rel)):
        assert fs.open(gs(copy_rel), "rb").read().decode("utf-8") == text, \
            f"{shared} changed since labels were made in {label_dir}. Save the new rules as a new version."
    else:
        with fs.open(gs(copy_rel), "wb") as f:
            f.write(text.encode("utf-8"))
    print(f"Prompt {version} (sha {hashlib.sha256(text.encode()).hexdigest()[:12]}): {len(DIMS)} dimensions, all found")
    return text


# ==================== 3. Gemini 打分 ====================
# Gemini 必须按这个格式回答（JSON）：能不能用、不能用的原因、16 个整数分、打分理由
Scores = create_model("Scores", **{d: (int, ...) for d in DIMS})

class ImageLabel(BaseModel):
    usable: bool
    unusable_reason: str
    scores: Scores
    rationale: str

class FatalAPIError(RuntimeError):
    pass                                                  # 设置问题（没登录、没权限、模型不存在）：立刻停下，免得白花钱

FATAL_HINTS = {401: "not logged in: gcloud auth application-default login",
               403: "Vertex AI API not enabled, no billing, or no permission",
               404: "model not available in this region: change --model or GOOGLE_CLOUD_LOCATION"}

def score_image(rel_path, prompt, min_score, tries=6):
    # 给一张图打分（一次 Gemini 请求）。min_score：街景 1，航拍图 0（0 = 从上面看不出来）
    data = fs.open(gs(rel_path), "rb").read()
    mime = "image/png" if rel_path.endswith(".png") else "image/jpeg"
    cfg = types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json", response_schema=ImageLabel,
                                      max_output_tokens=4096, thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET))
    last = None
    for k in range(tries):
        try:
            resp = client.models.generate_content(model=MODEL, contents=[types.Part.from_bytes(data=data, mime_type=mime), prompt], config=cfg)
            label = ImageLabel.model_validate_json(resp.text)
            sc = label.scores.model_dump()
            if label.usable:
                bad = {d: v for d, v in sc.items() if not min_score <= v <= 5}
                if bad:
                    raise ValueError(f"scores outside {min_score}-5: {bad}")
            else:
                sc = {d: 0 for d in sc}                   # 不能用的图：分数全记 0
            u = resp.usage_metadata
            return dict(usable=label.usable, unusable_reason=label.unusable_reason, **sc, rationale=label.rationale,
                        input_tokens=(u.prompt_token_count or 0) if u else 0,
                        output_tokens=((u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)) if u else 0,
                        scored_at=now())
        except genai_errors.APIError as e:
            if e.code in FATAL_HINTS:
                raise FatalAPIError(f"{e.code}: {FATAL_HINTS[e.code]}") from e
            if e.code == 400:                             # 只是这一张图的问题（比如图片坏了）：记为不能用，不重试，不算没打完
                return dict(usable=False, unusable_reason=f"Gemini refused this image (HTTP 400): {str(e)[:120]}", **{d: 0 for d in DIMS},
                            rationale="", input_tokens=0, output_tokens=0, scored_at=now())
            last = e; time.sleep(2 ** k)                  # 429 / 5xx：等 1、2、4、8、16、32 秒再试
        except ValueError as e:                           # 回答格式不对或分数超范围：再试
            last = e; time.sleep(1)
    raise RuntimeError(f"gave up on {rel_path}: {repr(last)[:200]}")

def score_all(todo, key, path_col, prompt, min_score, out_rel):
    # 批量打分：只打 out_rel 里还没有的图；同时发 WORKERS 个请求；每 50 张存一次档，断了重跑能接着打
    # 单张失败记下来跳过；设置问题（401 / 403 / 404）立刻全部停下
    done = pd.read_parquet(gs(out_rel)) if fs.exists(gs(out_rel)) else pd.DataFrame(columns=[key])
    todo = todo[~todo[key].isin(done[key])]
    if MAX_IMAGES:
        todo = todo.head(MAX_IMAGES)
    print(f"  {len(done)} already scored; {len(todo)} to score now")
    rows, errors, abort, fatal = [], {}, threading.Event(), None
    def save():
        nonlocal done
        if rows:
            done = pd.concat([done, pd.DataFrame(rows)], ignore_index=True).drop_duplicates(key, keep="last")
            done.to_parquet(gs(out_rel), index=False)
            rows.clear()
    def work(r):
        return None if abort.is_set() else {key: r[key], **score_image(r[path_col], prompt, min_score)}
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(work, r): r[key] for r in todo.to_dict("records")}
            for k, fut in enumerate(tqdm(as_completed(futs), total=len(futs), desc="scoring"), 1):
                try:
                    row = fut.result()
                    if row:
                        rows.append(row)
                except FatalAPIError as e:
                    fatal = e; abort.set()
                except Exception as e:
                    errors[futs[fut]] = repr(e)[:200]
                if k % 50 == 0:
                    save()
    finally:
        save()
    if fatal:
        raise fatal
    if errors:
        print(f"  {len(errors)} failed in this run, e.g.", list(errors.items())[:2])
    return done, errors


# ==================== 4. 航拍图打分（所有格子） ====================
def label_aerial(prompt):
    # 每个 bbox：给能用的航拍图打分 → tile_labels/；全部打完才存格子分 → cell_labels/
    # 换算：(分数 − 1) / 4 变成 0–1；0 分的维度和不能用的图记为空值（训练时跳过，不当成 0）
    aer_dir = f"{DS}/labels/{AERIAL_ID}"
    tile_list, aer_list = [], []
    for a in RUN:
        tiles = cells[(cells.area_id == a["area_id"]) & cells.tile_ok]
        print(f"{a['area_id']}: {len(tiles)} aerial tiles")
        raw, errors = score_all(tiles, "cell_id", "tile_path", prompt, 0, f"{aer_dir}/tile_labels/{a['area_id']}.parquet")
        if not check_done(a["area_id"], "aerial", tiles.cell_id, raw.cell_id, errors) or raw.empty:
            continue                                      # 没打完：格子分先不存（重跑补齐后再存）
        lab = tiles[["cell_id", "h3_index"]].merge(raw, on="cell_id", how="left")
        use = lab.usable.fillna(False).astype(bool)
        lab["n_images"], lab["n_usable"] = 1, use.astype(int)
        for d in DIMS:
            v = pd.to_numeric(lab[d])
            lab[d] = ((v - 1) / 4).where(use & (v >= 1))
            lab[f"{d}_std"] = lab[d] * 0                  # 每个格子只有一张航拍图：分散程度都是 0
        lab = lab[["cell_id", "h3_index", "n_images", "n_usable", *DIMS, *[f"{d}_std" for d in DIMS], "rationale"]].assign(updated_at=now())
        lab.to_parquet(gs(f"{aer_dir}/cell_labels/{a['area_id']}.parquet"), index=False)
        tile_list.append(raw); aer_list.append(lab)
    tile_scores = pd.concat(tile_list, ignore_index=True) if tile_list else pd.DataFrame(columns=["cell_id", "usable", *DIMS])
    aerial_labels = pd.concat(aer_list, ignore_index=True) if aer_list else pd.DataFrame(columns=["cell_id", "h3_index", *DIMS, "rationale"])
    print(f"\n{len(aerial_labels)} cells with aerial scores")
    return tile_scores, aerial_labels


# ==================== 5. 街景打分（只有有街景的格子） ====================
def label_street(prompt):
    # 每个城市 bbox：给选中的街景照片打分 → image_labels/（郊区没有街景，自动跳过）
    st_dir = f"{DS}/labels/{STREET_ID}"
    img_list = []
    for a in RUN:
        photos = sv[sv.area_id == a["area_id"]]
        if photos.empty:
            continue
        print(f"{a['area_id']}: {len(photos)} street photos")
        raw, errors = score_all(photos, "image_id", "image_path", prompt, 1, f"{st_dir}/image_labels/{a['area_id']}.parquet")
        check_done(a["area_id"], "street", photos.image_id, raw.image_id, errors)
        img_list.append(raw[raw.image_id.isin(photos.image_id)])
    mine = pd.concat(img_list, ignore_index=True) if img_list else pd.DataFrame(columns=["image_id", "usable", "rationale", *DIMS])
    mine["usable"] = mine.usable.astype(bool)
    for d in DIMS:
        mine[d] = pd.to_numeric(mine[d])

    # 格子的分 = 能用的照片（最多 4 张）每个维度取平均，再换算成 0–1；能用的照片少于 MIN_USABLE_IMAGES 张的格子记为空值
    joined = mine.merge(sv[["image_id", "cell_id", "h3_index", "area_id"]], on="image_id")
    g = joined[joined.usable].groupby(["cell_id", "h3_index", "area_id"])
    agg = g[DIMS].mean().join(g[DIMS].std(ddof=0).add_suffix("_std")).join(g.size().rename("n_usable")).reset_index()
    labels = sv[["cell_id", "h3_index", "area_id"]].drop_duplicates().merge(agg, on=["cell_id", "h3_index", "area_id"], how="left")
    labels["n_images"] = labels.cell_id.map(sv.groupby("cell_id").size()).fillna(0).astype(int)
    labels["n_usable"] = labels.n_usable.fillna(0).astype(int)
    enough = labels.n_usable >= MIN_USABLE_IMAGES
    for d in DIMS:
        labels[d] = ((labels[d] - 1) / 4).where(enough)
        labels[f"{d}_std"] = (labels[f"{d}_std"] / 4).where(enough)
    labels = labels[["cell_id", "h3_index", "area_id", "n_images", "n_usable", *DIMS, *[f"{d}_std" for d in DIMS]]]
    # 只存全部打完的 bbox（不然少打了几张的格子会被误当成“能用的照片不够”）
    for area_id, part in labels.groupby("area_id"):
        if step_status(area_id, "street") == "complete":
            part.drop(columns="area_id").assign(updated_at=now()).to_parquet(gs(f"{st_dir}/cell_labels/{area_id}.parquet"), index=False)

    tin, tout = int(mine.input_tokens.sum()) if len(mine) else 0, int(mine.output_tokens.sum()) if len(mine) else 0
    print(f"\n{len(mine)} photos scored ({int(mine.usable.sum())} usable); {len(labels)} cells, {int(enough.sum())} have street labels; about ${cost_usd(tin, tout):.3f}")
    return mine, labels


# ==================== 6. 汇总 ====================
def summary():
    # 每个标签文件夹写一个 meta.json：模型、规则版本、打了多少张、token 数、估算费用（统计 bucket 里全部 bbox，谁最后运行都对）
    fs.invalidate_cache()                                 # 清掉文件列表的缓存，刚写的文件才列得出来
    for label_id, version, folder in [(AERIAL_ID, AERIAL_PROMPT, "tile_labels"), (STREET_ID, STREET_PROMPT, "image_labels")]:
        files = fs.find(f"{BUCKET}/{DS}/labels/{label_id}/{folder}")
        with ThreadPoolExecutor(16) as ex:
            parts = list(ex.map(lambda p: pd.read_parquet(f"gs://{p}", columns=["usable", "input_tokens", "output_tokens"]), files))
        raw = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["usable", "input_tokens", "output_tokens"])
        tin, tout = int(raw.input_tokens.sum()), int(raw.output_tokens.sum())
        meta = {"label_id": label_id, "dataset_id": DS.split("/")[-1], "model": MODEL, "location": LOCATION,
                "prompt_version": version, "dimensions": DIMS, "temperature": 0.0, "thinking_budget": THINKING_BUDGET,
                "min_usable_images": MIN_USABLE_IMAGES, "bboxes": len(files), "images_scored": len(raw),
                "images_usable": int(raw.usable.astype(bool).sum()), "input_tokens": tin, "output_tokens": tout,
                "est_cost_usd": round(cost_usd(tin, tout), 4), "updated_at": now()}
        with fs.open(gs(f"{DS}/labels/{label_id}/meta.json"), "w") as f:
            json.dump(meta, f, indent=2)
        print(f"{label_id}: {meta['bboxes']} bboxes, {meta['images_scored']} images scored, estimated cost ${meta['est_cost_usd']}")

    # 未完成清单：打了分的图数 < 要打的图数就是没打完（谁跑的都算），附原因和可以直接复制的重跑参数
    prog, bad = progress(), {}
    print("\n" + prog.to_string())
    for area_id, r in prog.iterrows():
        for step, got, want in [("aerial", r.aerial_scored, r.tiles_ok), ("street", r.street_scored, r.street_photos)]:
            if got < want or step_status(area_id, step) == "incomplete":
                reason = STEPS.get(area_id, {}).get(step, {}).get("reason", f"{want - got} of {want} images not scored yet")
                bad.setdefault(area_id, []).append(f"{step}: {reason}")
    if bad:
        print(f"\n!!! {len(bad)} bbox(es) NOT fully scored (rerun them; scored images are not redone or paid again):")
        for area_id, reasons in bad.items():
            print(f"    {area_id:<28} {'; '.join(reasons)}")
        print(f"Rerun these (only your own): --areas {' '.join(bad)}")
    else:
        print("\nNo incomplete bboxes.")


# ==================== 7. 检查用的图（存到本地 qc/ 和 bucket 的 {DS}/qc/） ====================
def save_fig(name):
    plt.savefig(f"{QC_DIR}/{name}", dpi=100, bbox_inches="tight")
    plt.close()

def qc(tile_scores, aerial_labels, mine, labels):
    os.makedirs(QC_DIR, exist_ok=True)
    ok = mine[mine.usable]                                # 能用的街景
    label_map(aerial_labels, mine, labels)

    # compare_*.png：Gemini 的分和独立的地图数据（OSM / WorldCover）一致吗？每个点是一个格子，蓝 = 街景分，橙 = 航拍分
    # ρ 是排序一致程度：1 = 完全一致，0 = 没关系。城市只用有街景标签的格子（两种分比的是同一批格子）；郊区只有航拍分
    osm = read_areas("osm_features", [a["area_id"] for a in RUN])
    pairs = [("tree_canopy", "wc_tree_frac"), ("forest_environment", "wc_tree_frac"), ("grassland", "wc_grass_frac"),
             ("water_access", "osm_water_frac"), ("farmland_pastoral", "wc_crop_frac"), ("urban_density", "wc_built_frac"),
             ("road_width", "road_major_m"), ("commercial_district", "poi_count")]
    sources = {"street": (labels, "#4c78a8"), "aerial": (aerial_labels, "#e8853b")}
    street_cells = set(labels.cell_id[labels[DIMS].notna().any(axis=1)])
    for kind in ["city", "suburb"]:
        ids_area = [a["area_id"] for a in RUN if a["area_type"] == kind]
        ids = cells.cell_id[cells.area_id.isin(ids_area)]
        ids, names = (ids[ids.isin(street_cells)], ["street", "aerial"]) if kind == "city" else (ids, ["aerial"])
        if osm is None or not len(ids):
            continue
        fig, axes = plt.subplots(2, 4, figsize=(16, 7.5))
        fig.suptitle(f"all {kind}: {len(ids)} cells in {len(ids_area)} bbox(es)", fontsize=12)
        for ax, (d, f) in zip(axes.ravel(), pairs):
            title = []
            for name in names:
                table, color = sources[name]
                pair = table[table.cell_id.isin(ids)][["cell_id", d]].merge(osm[["cell_id", f]], on="cell_id").dropna().astype({d: float})
                rho = pair[d].rank().corr(pair[f].rank()) if pair[d].nunique() > 1 and pair[f].nunique() > 1 else float("nan")
                ax.scatter(pair[f], pair[d], s=16, alpha=0.6, color=color, label=name)
                title.append(f"{name} ρ={rho:.2f} (n={len(pair)})")
            ax.set_xlabel(f, fontsize=9); ax.set_ylabel(f"{d} (label)", fontsize=9)
            ax.set_title("   ".join(title), fontsize=8); ax.tick_params(labelsize=8)
        axes[0, 0].legend(fontsize=8)
        save_fig(f"compare_{kind}.png")

    # spread.png：每个维度街景和航拍在 1–5 上的分布（航拍不算 0 分）
    # 某个分数占 60% 以上或标准差小于 0.6 = low spread：这个维度区分不出不同的地方
    low = lambda vc, sd: "low spread" if len(vc) and (vc.max() > 0.6 or sd < 0.6) else ""
    aer_ok = tile_scores[tile_scores.usable.fillna(False).astype(bool)]
    rows = []
    fig, axes = plt.subplots(4, 4, figsize=(12, 8), sharey=True)
    for ax, d in zip(axes.ravel(), DIMS):
        s = ok[d]
        a = pd.to_numeric(aer_ok[d]); judged = a[a >= 1]
        vs = s.value_counts(normalize=True).reindex(range(1, 6)).fillna(0)
        va = judged.value_counts(normalize=True).reindex(range(1, 6)).fillna(0)
        ax.bar(np.arange(1, 6) - 0.2, vs.values, 0.4, color="#4c78a8", label="street")
        ax.bar(np.arange(1, 6) + 0.2, va.values, 0.4, color="#e8853b", label="aerial")
        ax.set_title(d, fontsize=8); ax.set_ylim(0, 1); ax.set_xticks(range(1, 6))
        rows.append({"dimension": d, "street mean": s.mean(), "street std": s.std(), "street": low(vs[vs > 0], s.std()),
                     "aerial mean": judged.mean(), "aerial std": judged.std(), "aerial": low(va[va > 0], judged.std()),
                     "aerial can't judge %": (a == 0).mean() * 100})
    axes[0, 0].legend(fontsize=7)
    save_fig("spread.png")
    print(pd.DataFrame(rows).set_index("dimension").round(2).to_string())

    # photos.png：随机看 4 张街景和它们的分数（只列出不是 1 分的维度），看看分数和图对不对得上
    look = ok.sample(n=min(4, len(ok)), random_state=42).merge(sv[["image_id", "image_path"]], on="image_id")
    if len(look):
        fig, axes = plt.subplots(len(look), 2, figsize=(12, 3.6 * len(look)), squeeze=False, gridspec_kw={"width_ratios": [1, 1.2]})
        for (ax_img, ax_txt), r in zip(axes, look.itertuples()):
            with fs.open(gs(r.image_path), "rb") as f:
                ax_img.imshow(Image.open(f))
            ax_img.axis("off")
            high = ", ".join(f"{d} {getattr(r, d)}" for d in DIMS if getattr(r, d) > 1)
            ax_txt.text(0, 1, textwrap.fill(high, 60) + "\n\n" + textwrap.fill(r.rationale, 60), va="top", fontsize=9, family="monospace")
            ax_txt.axis("off")
        save_fig("photos.png")

    for name in os.listdir(QC_DIR):
        fs.put(f"{QC_DIR}/{name}", gs(f"{DS}/{QC_DIR}/{name}"))
    print(f"QC files: local {QC_DIR}/ and gs://{BUCKET}/{DS}/{QC_DIR}/")

def label_map(aerial_labels, mine, labels):
    # label_map.html 交互地图：所有格子按一个维度上色，点格子在右边看它的街景分和航拍分
    # 左上角：BBox 跳到一个 bbox；Dimension 选维度；Scores from 切换街景分 / 航拍分（郊区只有航拍分）
    # 橙色边框 = 抽样格子：每个 bbox 切成 3×3 块，每块随机挑 1 个（城市只挑有街景的），点开能看航拍图、每张街景和 Gemini 的理由
    SAMPLE_GRID = 3
    SAMPLE_SEED = 0                                       # 想换一批样本就改这个数
    ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
    THUMB_PX = 120
    rnd = lambda v: round(float(v), 5)

    # 每个格子的分数存成 0–100 的整数（null = 没有分），文件小一些
    pct = lambda row: [None if np.isnan(v) else int(round(v * 100)) for v in row]
    S = dict(zip(labels.cell_id, map(pct, labels[DIMS].to_numpy(float))))
    A = dict(zip(aerial_labels.cell_id, map(pct, aerial_labels[DIMS].to_numpy(float))))

    # 抽样格子，分散在每个 bbox 里
    rng, samples = np.random.default_rng(SAMPLE_SEED), []
    for a in RUN:
        cand = cells[(cells.area_id == a["area_id"]) & cells.tile_ok]
        if a["area_type"] == "city":
            cand = cand[cand.cell_id.isin(sv.cell_id)]
        if cand.empty:
            continue
        lo0, la0, lo1, la1 = a["bbox"]
        bx = np.minimum(((cand.lon - lo0) / (lo1 - lo0) * SAMPLE_GRID).astype(int), SAMPLE_GRID - 1)
        by = np.minimum(((cand.lat - la0) / (la1 - la0) * SAMPLE_GRID).astype(int), SAMPLE_GRID - 1)
        for _, blk in cand.groupby([bx, by]):
            samples.append(blk.iloc[int(rng.integers(len(blk)))])
    samples = pd.DataFrame(samples)

    def thumb(path):
        # 小缩略图塞进网页（bucket 是私有的，浏览器不能直接读图）
        with fs.open(gs(path), "rb") as f:
            im = Image.open(f).convert("RGB")
        im.thumbnail((THUMB_PX, THUMB_PX))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=60)
        return base64.b64encode(buf.getvalue()).decode()
    ps = sv[sv.cell_id.isin(samples.cell_id)].sort_values("compass").merge(mine[["image_id", "usable", "rationale", *DIMS]], on="image_id", how="left")
    jobs = list(zip(samples.cell_id, samples.tile_path)) + list(zip(ps.image_id, ps.image_path))
    with ThreadPoolExecutor(16) as ex:
        IMG = dict(zip([k for k, _ in jobs], ex.map(lambda j: thumb(j[1]), jobs)))

    # 只有抽样格子带详情：w = Gemini 给航拍图的理由，p = 照片 [编号, 说明, 能不能用, 16 个分, 理由]
    WHY = dict(zip(aerial_labels.cell_id, aerial_labels.rationale.fillna("")))
    X = {c: {"w": WHY.get(c, ""), "p": []} for c in samples.cell_id}
    for p in ps.to_dict("records"):
        ok_ = bool(p["usable"]) if pd.notna(p["usable"]) else False
        X[p["cell_id"]]["p"].append([p["image_id"], f"{p['compass']:.0f}° · {str(p['captured_at'])[:7]}", ok_,
                                     [int(p[d]) for d in DIMS] if ok_ else None, p["rationale"] if ok_ else ""])

    m = folium.Map(tiles=None, prefer_canvas=True)
    folium.TileLayer(ESRI.format("World_Imagery"), attr="Esri", name="Basemap: satellite", overlay=True, show=False).add_to(m)
    folium.TileLayer(ESRI.format("World_Street_Map"), attr="Esri", name="Basemap: street map", overlay=True, show=True).add_to(m)
    def hex_ring(h):
        ring = [[rnd(lo), rnd(la)] for la, lo in h3.cell_to_boundary(h)]
        return [ring + ring[:1]]
    geo = folium.GeoJson({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": r.cell_id}, "geometry": {"type": "Polygon", "coordinates": hex_ring(r.h3_index)}}
        for r in cells.itertuples()]}, name="H3 cells (orange outline = sample with pictures)",
        style_function=lambda f: {"color": "#ffffff", "weight": 0.6, "fillColor": "#bdbdbd", "fillOpacity": 0.75}).add_to(m)

    # bbox 边框用线画（用矩形会挡住里面格子的点击）
    outlines = folium.FeatureGroup(name="BBox outlines (red = city, blue = suburb)").add_to(m)
    bounds = {}
    for a in RUN:
        lo0, la0, lo1, la1 = a["bbox"]
        bounds[a["area_id"]] = [[la0, lo0], [la1, lo1]]
        folium.PolyLine([(la0, lo0), (la0, lo1), (la1, lo1), (la1, lo0), (la0, lo0)], color="#d1342a" if a["area_type"] == "city" else "#2f6f9f",
                        weight=2, tooltip=f"{a['area_id']} · {a['area_subtype']}").add_to(outlines)

    # 页面布局：左边地图（64%），右边详情面板，左上角下拉框
    box = "position:fixed;left:10px;z-index:9999;background:white;padding:6px 9px;border-radius:6px;font:12px sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3)"
    m.get_root().header.add_child(folium.Element(
        "<style>.folium-map{width:64%!important}"
        "#cellpanel{position:absolute;top:0;right:0;width:36%;height:100%;overflow:auto;box-sizing:border-box;padding:10px;"
        "border-left:1px solid #ccc;background:#fff;font:12px sans-serif}#cellpanel td,#cellpanel th{text-align:left;padding:1px 4px}"
        f".pic{{display:inline-block;width:{THUMB_PX}px;margin:2px;vertical-align:top;font-size:11px}}.pic img{{width:100%}}</style>"))
    m.get_root().html.add_child(folium.Element(
        '<div id="cellpanel"><b>Click a cell</b> on the map to see its scores here. Orange cells also show their pictures.</div>'
        f'<div style="{box};top:90px"><b>BBox</b><br><select onchange="zoomTo(this.value)">'
        + "".join(f'<option value="{a["area_id"]}">{a["area_id"]} ({a["area_subtype"]})</option>' for a in RUN) + "</select><br>"
        "<b>Dimension</b><br><select onchange=\"dim = +this.value; recolor()\">"
        + "".join(f'<option value="{i}">{d}</option>' for i, d in enumerate(DIMS)) + "</select><br>"
        '<b>Scores from</b><br><label><input type="radio" name="src" onchange="fromAerial = 0; recolor()"> street view</label><br>'
        '<label><input type="radio" name="src" checked onchange="fromAerial = 1; recolor()"> aerial tile</label></div>'
        f'<div style="{box};bottom:18px">label: '
        + "".join(f'<span style="background:{to_hex(plt.cm.viridis(v))};padding:0 7px;margin-right:1px"></span>' for v in np.linspace(0, 1, 9))
        + ' &nbsp;1 point → 5 points &nbsp;<span style="background:#bdbdbd;padding:0 7px"></span> no score'
        + ' &nbsp;<span style="border:2px solid #ff6a00;padding:0 6px"></span> sample with pictures</div>'))
    # 网页里的小程序：按选中的维度给格子上色，点格子时在右边显示分数和图片
    m.get_root().script.add_child(folium.Element(
        f"var S = {json.dumps(S)}, A = {json.dumps(A)}, X = {json.dumps(X)}, IMG = {json.dumps(IMG)}, DIMS = {json.dumps(DIMS)}, BOUNDS = {json.dumps(bounds)};"
        f"var LUT = {json.dumps([to_hex(plt.cm.viridis(i / 255)) for i in range(256)])};"
        """
        var GEO, dim = 0, fromAerial = 1, last = null;
        function col(v) { return v == null ? '#bdbdbd' : LUT[Math.round(v / 100 * 255)]; }
        function look(l) {
          var id = l.feature.properties.id, v = (fromAerial ? A : S)[id];
          return {fillColor: col(v ? v[dim] : null), color: X[id] ? '#ff6a00' : '#ffffff', weight: X[id] ? 3 : 0.6};
        }
        function recolor() { GEO.eachLayer(function (l) { l.setStyle(look(l)); }); if (last) last.setStyle({color: '#ff1f1f', weight: 3}); }
        function fmt(v, n) { return v == null ? '–' : (v / 100 * 4 + 1).toFixed(n); }
        function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
        function pic(key, caption, why) {
          return IMG[key] ? '<div class="pic"><img src="data:image/jpeg;base64,' + IMG[key] + '" title="' + esc(why) + '"><br>' + caption + '</div>' : '';
        }
        function showCell(id, layer) {
          var s = S[id] || [], a = A[id] || [], x = X[id] || {w: '', p: []};
          var h = '<b>' + id + '</b>' + (X[id] ? ' · ' + x.p.length + ' street photos<br>' + pic(id, '<b>aerial tile</b>', x.w)
                                               : '<br><span style="color:#777">Pictures only for the orange sample cells.</span><br>');
          x.p.forEach(function (p, k) { h += pic(p[0], 'P' + (k + 1) + ' · ' + p[1] + (p[2] ? '' : ' · <b style="color:#d1342a">unusable</b>'), p[4]); });
          h += '<table><tr><th>dimension</th><th>street avg</th><th>aerial</th>' + x.p.map(function (p, k) { return '<th>P' + (k + 1) + '</th>'; }).join('') + '</tr>';
          DIMS.forEach(function (d, i) {
            h += '<tr><td>' + d + '</td><td><b>' + fmt(s[i], 2) + '</b></td><td><b>' + fmt(a[i], 0) + '</b></td>'
               + x.p.map(function (p) { return '<td>' + (p[3] ? p[3][i] : '–') + '</td>'; }).join('') + '</tr>';
          });
          document.getElementById('cellpanel').innerHTML = h + '</table><div style="color:#777">Scores 1–5; – = no score '
            + '(aerial: cannot be judged from above). Hover an image to read Gemini\\'s reason.</div>';
          if (last) last.setStyle(look(last));
          layer.setStyle({color: '#ff1f1f', weight: 3}); layer.bringToFront(); last = layer;
        }
        """
        + f"function zoomTo(id) {{ {m.get_name()}.fitBounds(BOUNDS[id]); }}"
        + f"window.addEventListener('load', function () {{ GEO = {geo.get_name()}; "
        + "GEO.eachLayer(function (l) { l.on('click', function () { showCell(l.feature.properties.id, l); }); }); recolor(); });"))
    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds(bounds[RUN[0]["area_id"]])
    m.save(f"{QC_DIR}/label_map.html")
    print(f"Map saved: {QC_DIR}/label_map.html ({os.path.getsize(f'{QC_DIR}/label_map.html') / 1e6:.1f} MB), "
          f"{len(samples)} sample cells with pictures. Open it in Chrome / Safari.")


# ==================== 入口 ====================
def main():
    load_dotenv(find_dotenv(usecwd=True))                 # 本地读 .env；容器里用 --env-file 传，没有 .env 也没关系
    p = argparse.ArgumentParser(description="GreenPath data labeler")
    p.add_argument("--dataset", required=True, help="要打分的数据集（data-collector 做出来的），例如 1026_Full_data")
    p.add_argument("--areas", nargs="+", required=True, help="这次给哪些 bbox 打分（area_id，可以写多个），或 all")
    p.add_argument("--aerial-prompt", default="a5", help="航拍图用的规则版本：prompts/{版本}.txt")
    p.add_argument("--street-prompt", default="v7", help="街景用的规则版本：prompts/{版本}.txt")
    p.add_argument("--model", default="gemini-2.5-flash", help="group project 能用 gemini-2.5-flash")
    p.add_argument("--max-images", type=int, default=None, help="每个 bbox、每种图这次最多新打几张（不写 = 全打）")
    p.add_argument("--workers", type=int, default=8, help="同时发几个请求；报 429（请求太多）就调小")
    p.add_argument("--bucket", default=os.environ.get("GREENPATH_BUCKET"), help="默认读环境变量 GREENPATH_BUCKET")
    p.add_argument("--no-qc", action="store_true", help="不画检查用的图和地图（通宵跑时用）")
    args = p.parse_args()
    if not args.bucket:
        p.error("no bucket: pass --bucket or set GREENPATH_BUCKET")

    setup(args);                                          tick("load dataset")
    aerial_text = load_prompt(AERIAL_PROMPT, f"{DS}/labels/{AERIAL_ID}")
    street_text = load_prompt(STREET_PROMPT, f"{DS}/labels/{STREET_ID}")
    tile_scores, aerial_labels = label_aerial(aerial_text); tick("aerial")
    mine, labels = label_street(street_text);             tick("street")
    summary();                                            tick("summary")
    if not args.no_qc:
        qc(tile_scores, aerial_labels, mine, labels);     tick("qc")
    print("\nTime per step (minutes):", ", ".join(f"{k} {v:.1f}" for k, v in TIMES.items()), f"| total {(time.time() - T0) / 60:.1f}")


if __name__ == "__main__":
    main()
