"""
模型服务的"后厨"：启动时把模型和格子索引准备好，之后收到格子或图片就打分。main.py 只负责收请求、回结果。

启动时（import 这个文件时，只做一次，约 10–20 秒）：
    1. 读 artifacts/config.json：这是哪个模型（run_id）、15 个分数的名字和顺序
    2. 搭模型、装上训练好的权重。用的是训练时的 network.py（--prepare 复制进 artifacts/），预处理和训练完全一样
    3. 建格子索引：读 DATASETS 里每个数据集的 cells/*.parquet（collector 写的），记下每个 H3 格子对应 bucket 里哪张航拍图

artifacts/ 里的 3 个文件由 model-deployment 的 `python cli.py --prepare` 准备，build 时打进镜像：
    best_model.pt   训练好的权重     config.json   模型信息     network.py   搭模型、预处理的代码
"""
import io
import json
import os

import gcsfs
import pandas as pd
import torch
from PIL import Image

from artifacts import network as N    # 训练用的同一份 network.py：搭模型、预处理、装权重

BUCKET = os.environ["GREENPATH_BUCKET"]
DATASETS = os.environ["DATASETS"].split(",")      # 能打分的区域 = 这些数据集里下载好航拍图的格子，例如 1026_Full_data
BATCH_SIZE = 64                                   # 一次让模型算几张图

# 用几个 CPU 线程算：Cloud Run 上由 --deploy 设成这个实例分到的 CPU 数；本地不设，用 torch 的默认
if os.environ.get("TORCH_THREADS"):
    torch.set_num_threads(int(os.environ["TORCH_THREADS"]))

# 第 1 步：模型信息
CONFIG = json.load(open("artifacts/config.json"))
RUN_ID = CONFIG["run_id"]                         # 例如 dinov2_s-linear-seed42-ebcc811f
TRAINING_ID = CONFIG["settings"]["training_id"]   # 用哪个训练集训练的，例如 full_v2
TARGETS = CONFIG["targets"]                       # 15 个分数的名字，顺序 = 模型输出的顺序

# 第 2 步：模型。先搭出完整的 DINOv2 + 线性层，再换上训练过的部分；eval() = 只打分、不训练
MODEL = N.build_model(len(TARGETS))
N.load_weights(MODEL, "artifacts/best_model.pt")
MODEL.eval()
TRANSFORM = N.eval_transform()                    # 预处理：缩到 224×224、标准化

# 读写 bucket 的工具。身份：本地 = 挂进来的 gcloud 登录；Cloud Run = 服务挂的服务账号
FS = gcsfs.GCSFileSystem(token="google_default")


# 第 3 步：格子索引。输出一张表：行 = H3 格子，列 = tile_path（航拍图在 bucket 里的路径）、tile_ok（图能不能用）、tile_reason（为什么不能用）
def load_index():
    files = [f for d in DATASETS for f in FS.glob(f"{BUCKET}/datasets/{d}/cells/*.parquet")]   # 每个区域一个文件
    assert files, f"no cells found in gs://{BUCKET}/datasets/{{{','.join(DATASETS)}}}/cells/"
    data = FS.cat(files)                                          # 并行下载这些小文件：{路径: 文件字节}
    cells = pd.concat(pd.read_parquet(io.BytesIO(b), columns=["h3_index", "tile_path", "tile_ok", "tile_reason"]) for b in data.values())
    # 同一个格子出现在两个数据集里时，留能用的那张图
    return cells.sort_values("tile_ok", ascending=False).drop_duplicates("h3_index").set_index("h3_index")


INDEX = load_index()
print(f"model {RUN_ID} ready · {len(TARGETS)} scores · {len(INDEX):,} cells from {DATASETS}", flush=True)


# 一批图片（PNG 文件的原始字节）→ 每张图一组分数 {分数名: 0–1}
# 和训练时一样：打开图片 → 转 RGB → 预处理 → 模型；每 64 张算一次
@torch.inference_mode()                           # 只打分：不记梯度，更快更省内存
def score_images(images):
    rows = []
    for i in range(0, len(images), BATCH_SIZE):
        x = torch.stack([TRANSFORM(Image.open(io.BytesIO(b)).convert("RGB")) for b in images[i:i + BATCH_SIZE]])
        rows += MODEL(x).tolist()
    return [dict(zip(TARGETS, (round(v, 4) for v in row))) for row in rows]


# 一批 H3 格子 → 每个格子一条结果，顺序和请求一样：{"h3_index", "status", "scores"}
# status：ok = 打了分；not_downloaded = 不在 DATASETS 的格子里；
#         no_naip_coverage / mostly_black = collector 判定这格的航拍图不能用（没有 NAIP / 大部分是黑的），不打分，scores 为 null
def score_cells(h3_indices):
    found = INDEX.reindex(h3_indices)                             # 按请求的顺序查索引；查不到的格子整行为空
    usable = found.tile_ok.eq(True).to_numpy()                    # 有图、图能用
    status = found.tile_reason.fillna("not_downloaded").where(~usable, "ok")
    paths = [f"{BUCKET}/{p}" for p in found.tile_path[usable]]
    images = FS.cat(paths)                                        # 从 bucket 并行下载这些图：{路径: 图片字节}
    scores = iter(score_images([images[p] for p in paths]))
    return [{"h3_index": h, "status": s, "scores": next(scores) if u else None}
            for h, s, u in zip(h3_indices, status, usable)]
