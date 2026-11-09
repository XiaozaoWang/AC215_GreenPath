"""
GreenPath 训练（原 F2_train notebook）：读 data-processor 做好的训练表，训练一个模型——输入一张航拍图，输出 15 个 0–1 的分数。
这个文件在 Vertex AI 临时开的训练机器上运行（流程的训练步骤 workflow/model.py 提交任务，机器开机后运行 python -m trainer.task），本地一般不直接运行。

两种用法：
1. dry run（默认）：python -m trainer.task --bucket <bucket> --training-id <id>
   只抽 96 个训练格 + 48 个验证格，跑 2 轮，CPU 几分钟。只检查"读数据 → 训练 → 存结果"整条路通不通，结果不能拿来部署
2. 正式训练：python -m trainer.task --bucket <bucket> --training-id <id> --no-dry-run
   全部 train + val，最多 25 轮，必须有 NVIDIA GPU（CPU 上一轮要几个小时，会直接报错停下）

输入：gs://<bucket>/training/<training-id>/dataset.parquet、meta.json（data-processor 写的）+ 表里 tile_path 指向的航拍图（data-collector 写的）
输出：gs://<bucket>/results/<training-id>/{dry|final}/<run_id>/ 下 5 个文件（best_model.pt 就是部署要用的模型），外加 latest.json
     有环境变量 WANDB_API_KEY 时，训练曲线同时记到 W&B 的 greenpath 项目；没有就只存在 history.parquet 里
"""
import argparse
import datetime
import hashlib
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import gcsfs
import numpy as np
import pandas as pd
import timm
import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset

from trainer import network as N    # 神经网络本身（建模型、预处理、存取权重）在 network.py，部署也用它

# 训练设置：都是实验里比较过、选定的最终配置（来源见 1107-vertexAI 的交接说明），平时不用改
SEED = 42                    # 随机种子：同样的种子，抽样、初始化、打乱顺序每次都一样，结果可以复现
BATCH_SIZE = 16              # 一次喂给模型几张图（一个 batch），算一次误差、更新一次参数
PATIENCE = 5                 # 验证 MAE 连续 5 轮不提升就提前停（early stopping），防止越练越差
HEAD_LR = 3e-4               # 线性层的学习率：每次更新参数迈多大一步
ENCODER_LR = 1e-5            # 解冻的编码器层的学习率：小 30 倍，只微调，不破坏预训练学到的东西
WEIGHT_DECAY = 1e-4          # 权重衰减：让参数不要变得太大，减少过拟合
CLIP_NORM = 1.0              # 梯度裁剪：一步更新的幅度最多这么大，防止某个离谱的 batch 把模型带偏
UNFREEZE_BLOCKS = 2          # 解冻 DINOv2 最后 2 个 transformer 块（其余 10 块冻住不动）

DRY_CELLS = {"train": 96, "val": 48}       # dry run 每个 split 抽多少格子
TILE_DIR = "/tmp/greenpath/tiles"          # 航拍图先从 bucket 复制到训练机器的这个临时目录
RUNS_DIR = "/tmp/greenpath/runs"           # 训练结果先写在机器上，结束后复制到 bucket（机器用完就销毁）
TOLERANCES = {0.05: "acc05", 0.10: "acc10", 0.20: "acc20"}   # 容差准确率：预测和标准答案差在 ±0.05 / 0.10 / 0.20 以内的比例


# 定义 4 个命令行参数
# 在 Vertex 上由流程的训练步骤（workflow/model.py）传进来：--bucket=... --training-id=... --dry-run
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="GreenPath training")

    # 读哪：bucket 名 + 训练集名，两个拼出 gs://<bucket>/training/<training-id>/
    p.add_argument("--bucket", default=os.environ.get("GREENPATH_BUCKET"), help="默认读环境变量 GREENPATH_BUCKET（Vertex 上没有，要传）")
    p.add_argument("--training-id", required=True, help="training/<training-id>/，data-processor 做出来的训练集")

    # 怎么跑：试跑还是正式；最多几轮
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True, help="默认 dry run；--no-dry-run = 正式训练（要 GPU）")
    p.add_argument("--epochs", type=int, default=25, help="最多几轮（dry run 固定 2 轮）")

    a = p.parse_args(argv)
    if not a.bucket:
        p.error("no bucket: pass --bucket or set GREENPATH_BUCKET")

    # dry run 只跑 2 轮，2 轮不提升就停
    a.patience = PATIENCE
    if a.dry_run:
        a.epochs, a.patience = 2, 2
    return a


'''
main 按顺序跑 6 步（前 5 步都在训练机器的本地做，第 6 步才写 bucket）：
第 1 步 load_training_set：从 bucket 读 data-processor 写的训练表，丢掉 Test；dry run 时抽一小部分
第 2 步 download_tiles：把要用的航拍图从 bucket 复制到机器上（训练时每轮都要读一遍图，从本地读快得多）
第 3 步 建模型和优化器：network.build_model 搭出 DINOv2-S + 线性层，只有解冻的部分会被训练
第 4 步 训练循环：一轮（epoch）= 把所有训练图过一遍；每轮结束在验证集上算 MAE，最好的一轮存成 best_model.pt
第 5 步 用最好的一轮重算验证集，写出 5 个结果文件
第 6 步 复制到 bucket，更新 latest.json（告诉部署"最新的模型是哪个"）
'''


# ==================== 第 1–2 步：读数据 ====================

# 第 1 步：读训练表。输入 gs://<bucket>/training/<training-id>/（data-processor 写的 dataset.parquet + meta.json）
# 输出：15 个目标名、{"train": 训练格子表, "val": 验证格子表}、dataset.parquet 的版本号
def load_training_set(fs, bucket, training_id, dry_run):
    root = f"gs://{bucket}/training/{training_id}"

    # 15 个目标名从 meta.json 取（processor 记在 columns.targets 里），代码里不写死
    dims = json.load(fs.open(f"{root}/meta.json"))["columns"]["targets"]
    # 训练表每个格子一行；按 cell_id 排好序，保证每次读进来的顺序一样
    ds = pd.read_parquet(f"{root}/dataset.parquet").sort_values("cell_id").reset_index(drop=True)

    # 检查：格子不重复、至少有 train 和 val（单区域试跑的训练集没有 test，也可以）、目标分都在 0–1
    assert ds.cell_id.is_unique and {"train", "val"} <= set(ds.split), "need unique cells and both train and val"
    assert ds[dims].min().min() >= 0 and ds[dims].max().max() <= 1, "scores must be between 0 and 1"

    # 丢掉 Test：训练从头到尾看不到测试格子，测试成绩才可信
    ds = ds[ds.split != "test"].reset_index(drop=True)

    # dry run：train、val 各抽一小部分（random_state=0：每次抽到的都是同一批）
    if dry_run:
        ds = pd.concat([g.sample(min(DRY_CELLS[s], len(g)), random_state=0) for s, g in ds.groupby("split")]).sort_values("cell_id").reset_index(drop=True)

    # 拆成两张表：train 用来训练，val 用来每轮打分、挑最好的一轮
    frames = {s: ds[ds.split == s].reset_index(drop=True) for s in ["train", "val"]}
    print(f"{training_id}: {len(dims)} scores · " + " · ".join(f"{s} {len(f):,}" for s, f in frames.items()) + (" (dry run subset)" if dry_run else ""), flush=True)

    # 版本号（generation）：bucket 里的文件每被改写一次就变一次。记进 config.json，以后能查到这个模型是用哪一版训练表训练的
    return dims, frames, fs.info(f"{root}/dataset.parquet")["generation"]


# 第 2 步：下载航拍图。输入：训练表里的 tile_path（bucket 里的相对路径，例如 cache/naip_tiles/154m_256px/.../8a2b...fff.png）
# 输出：一个小函数 local，把 bucket 路径换成机器上的本地路径（DataLoader 读图时用）
def download_tiles(fs, bucket, paths):
    os.makedirs(TILE_DIR, exist_ok=True)
    local = lambda rel: os.path.join(TILE_DIR, os.path.basename(rel))    # 文件名就是格子的 H3 编号，不会重名
    todo = [rel for rel in sorted(set(paths)) if not os.path.exists(local(rel))]   # 已经下载过的跳过
    print(f"{len(set(paths)):,} pictures needed, {len(todo):,} to download", flush=True)

    def fetch(rel):
        fs.get_file(f"gs://{bucket}/{rel}", local(rel) + ".part")
        os.replace(local(rel) + ".part", local(rel))                 # 下载完才改名，中途断了不会留下半张图

    # 16 个线程同时下载
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(fetch, todo))
    return local


# 训练时一张一张取图的"取图员"：DataLoader 问它要第 i 张，它读图、预处理，交出 (图片张量, 15 个目标分)
class Tiles(Dataset):

    def __init__(self, paths, y, transform, augment=False):
        # paths：本地图片路径；y：每张图的 15 个目标分（缺的是 NaN）；transform：network.eval_transform 的预处理；augment：是否随机变换
        self.paths, self.y, self.tf, self.augment = list(paths), np.asarray(y, dtype=np.float32), transform, augment

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, i):
        # 读图 → 转成 RGB → 预处理（缩到 224×224、标准化）
        with Image.open(self.paths[i]) as im:
            x = self.tf(im.convert("RGB"))
        # 数据增强（只在训练时）：随机左右翻、上下翻、转 0/90/180/270°。航拍图怎么转都还是同一块地，
        # 这样等于多了几倍的训练图，模型不会死记"树在左上角"
        if self.augment:
            if random.random() < 0.5:
                x = x.flip(-1)
            if random.random() < 0.5:
                x = x.flip(-2)
            x = torch.rot90(x, random.randint(0, 3), (-2, -1))
        return x, torch.from_numpy(self.y[i].copy())


# ==================== 指标（第 4、5 步用）====================
# 缺的标准答案（NaN，例如没有街景的格子没有 visual_clutter）直接跳过，不当成 0

# 输入：P = 模型的预测（每个格子为一行，15列），frame = 同一批格子的标准答案表
# 输出：放在row里，每个维度一行：统计这一个维度有几个标准答案、MAE（平均差多少，越小越好）、容差准确率（越大越好）
def metric_table(P, frame, dims):
    rows = []
    for j, d in enumerate(dims):
        y = frame[d].to_numpy(dtype=np.float64)
        ok = ~np.isnan(y)                     # 只看有标准答案的格子
        err = np.abs(y[ok] - P[ok, j])        # 每个格子预测和标准答案差多少
        rows.append(dict(dimension=d, n=int(ok.sum()), mae=err.mean() if ok.any() else np.nan,
                         **{c: (err <= t + 1e-6).mean() if ok.any() else np.nan for t, c in TOLERANCES.items()}))
    return pd.DataFrame(rows)


# 把上面把 15 个维度再平均一次，得到的是“这个模型整体的成绩”：val_mae、val_acc05、val_acc10、val_acc20（选最好的一轮只看 val_mae）
def summary(T):
    return {f"val_{c}": float(T[c].mean()) for c in ["mae", *TOLERANCES.values()]}


# ==================== 第 3–6 步：训练 ====================

# 让模型给一批图打分（不训练、不更新参数），返回每张图 15 个 0–1 的数
@torch.no_grad()
def predict(model, dl, device, use_amp, amp_dtype):
    model.eval()                              # 切到"考试模式"
    out = []
    for xb, _ in dl:
        with torch.autocast(device_type="cuda", dtype=amp_dtype, enabled=use_amp):
            out.append(model(xb.to(device, non_blocking=True)).float().cpu().numpy())
    return np.concatenate(out)


# 损失函数：衡量一个 batch 预测得有多差，训练就是让它越来越小
# 用 Huber（smooth L1）：小误差按平方算、大误差按直线算。标准答案是 Gemini 打的分，偶尔离谱，Huber 不会被这种点牵着走
# 只算有标准答案的格子的维度；整个 batch 一个标准答案都没有时返回 0
def masked_loss(pred, target):
    ok = torch.isfinite(target)
    return nn.functional.smooth_l1_loss(pred[ok], target[ok], beta=0.1) if ok.any() else pred.sum() * 0


# 程序入口：读参数 → 第 1–6 步
def main(argv=None):
    a = parse_args(argv)
    fs = gcsfs.GCSFileSystem()                # 读写 bucket 的工具；身份在 Vertex 上 = workflow/model.py 指定的服务账号
    out_prefix = f"gs://{a.bucket}/results/{a.training_id}/{'dry' if a.dry_run else 'final'}"   # 结果写到哪（dry run 和正式训练分开放）

    # 设备：有 NVIDIA GPU 就用 GPU，并开混合精度（部分计算用 16 位浮点，更快更省显存；T4 没有快速的 bfloat16，用 float16）
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    use_amp = device.type == "cuda"
    amp_dtype = torch.bfloat16 if (use_amp and torch.cuda.get_device_capability()[0] >= 8) else torch.float16
    print("Device:", device, flush=True)

    # 正式训练却没有 GPU：直接停（CPU 上一轮要几个小时，白花钱）
    if not use_amp and not a.dry_run:
        raise RuntimeError("No NVIDIA GPU found. A real run needs a GPU; use --dry-run to check the plumbing on a CPU.")
    num_workers = 2 if (sys.platform == "linux" and not a.dry_run) else 0    # 正式训练时用 2 个子进程读图，GPU 不用等

    # 第 1–2 步：读训练集、下载图片；预处理和部署时用的是同一个（network.eval_transform）
    dims, frames, data_version = load_training_set(fs, a.bucket, a.training_id, a.dry_run)
    local = download_tiles(fs, a.bucket, list(frames["train"].tile_path) + list(frames["val"].tile_path))
    tf = N.eval_transform()

    # 把一张格子表变成 DataLoader：每次交出一个 batch（16 张图 + 16 组目标分）（比如说我一共有 8 个 batch，epoch是2，那就一共要给它送 16 次）
    # train：每轮打乱顺序 + 数据增强；val：固定顺序、不增强（每轮在同一套题上考试，分数才能比较）
    def loader(split):
        augment = split == "train"
        g = torch.Generator()
        g.manual_seed(SEED)                   # 打乱顺序也用固定种子，可以复现
        f = frames[split]
        return DataLoader(Tiles([local(p) for p in f.tile_path], f[dims].to_numpy(dtype=np.float32), tf, augment=augment), batch_size=BATCH_SIZE, shuffle=augment,
                          num_workers=num_workers, pin_memory=use_amp, generator=g if augment else None)

    # 这次训练的全部设置：写进 config.json 和 best_model.pt，以后能查到模型是怎么训练出来的
    # run_id = 设置的哈希（和 notebook 用同样的键），设置一样名字就一样，例如 dinov2_s-linear-seed42-ebcc811f
    settings = dict(training_id=a.training_id, data=data_version, backbone=N.BACKBONE, source=N.SOURCE,
                    unfrozen=f"last {UNFREEZE_BLOCKS} transformer blocks + final norm", head="linear", seed=SEED,
                    batch_size=BATCH_SIZE, epochs=a.epochs, patience=a.patience, head_lr=HEAD_LR, encoder_lr=ENCODER_LR, weight_decay=WEIGHT_DECAY, clip_norm=CLIP_NORM,
                    precision=str(amp_dtype).replace("torch.", "") if use_amp else "none", preprocessing=N.PREPROCESSING, dry_run=a.dry_run)
    rid = f"{N.BACKBONE}-linear-seed{SEED}-{hashlib.md5(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:8]}" #按配方卡起的名字，rid = "dinov2_s-linear-seed42-" + 配方卡的"指纹"前 8 位
    rdir = os.path.join(RUNS_DIR, rid) 
    os.makedirs(rdir, exist_ok=True) #在训练机器上建文件夹，训练中的 best_model.pt 等 5 个文件先写在这里。
    print(f"run: {rid}", flush=True)

    # 第 3 步：建模型和优化器
    # 先固定所有随机种子（Python、numpy、PyTorch），线性层的初始值、数据增强每次都一样
    random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
    if use_amp:
        torch.cuda.manual_seed_all(SEED)
    model = N.build_model(len(dims), UNFREEZE_BLOCKS).to(device)
    
    # 优化器 AdamW：按损失函数的梯度更新参数。两组参数两个学习率：解冻的编码器层小步微调，线性层大步学习；冻住的层不放进来，不会被改
    opt = torch.optim.AdamW([{"params": [p for p in model.encoder.parameters() if p.requires_grad], "lr": ENCODER_LR},
                             {"params": model.head.parameters(), "lr": HEAD_LR}], weight_decay=WEIGHT_DECAY)
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp and amp_dtype == torch.float16)   # float16 时放大损失，防止梯度太小变成 0；CPU 上不起作用
    tl, vl = loader("train"), loader("val")
    names = N.trainable_names(model)          # 被训练的参数名，存 best_model.pt 时只存这些

    # W&B：有 key 才记录训练曲线（网页上能看），没有就照常训练
    wb, key = None, os.environ.get("WANDB_API_KEY")
    if key:
        import wandb
        wandb.login(key=key)
        group = "dry" if a.dry_run else "final"
        wb = wandb.init(project="greenpath", group=group, name=rid, tags=[group, a.training_id], config=settings)
    else:
        print("no WANDB_API_KEY: this run is NOT logged to Weights & Biases")

    # 第 4 步：训练循环。一轮 = 把训练图按 batch 全部过一遍：预测 → 算损失 → 算梯度 → 更新参数
    # 每轮结束在验证集上考一次：MAE 比之前最好的还好，就把这一轮的权重存成 best_model.pt；连续 patience 轮没进步就提前停
    ckpt, best, bad, hist, t_start = os.path.join(rdir, "best_model.pt"), float("inf"), 0, [], time.time()
    for ep in range(1, a.epochs + 1):
        model.train()                         # 切到"训练模式"
        t0, loss_sum, steps = time.time(), 0.0, 0
        for xb, yb in tl:
            xb, yb = xb.to(device, non_blocking=True), yb.to(device, non_blocking=True)
            opt.zero_grad(set_to_none=True)   # 清掉上一步的梯度
            with torch.autocast(device_type="cuda", dtype=amp_dtype, enabled=use_amp):
                out = model(xb)               # 预测：16 张图 × 15 个分数
            loss = masked_loss(out.float(), yb)
            scaler.scale(loss).backward(); scaler.unscale_(opt)   # 反向传播：算出每个参数该往哪边改
            nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], CLIP_NORM)
            scaler.step(opt); scaler.update()                     # 按梯度更新参数
            loss_sum += float(loss.detach()); steps += 1

        # 这一轮的成绩：训练损失 + 验证集指标，记进 history（最后写成 history.parquet）
        row = dict(epoch=ep, train_loss=loss_sum / max(steps, 1), **summary(metric_table(predict(model, vl, device, use_amp, amp_dtype), frames["val"], dims)),
                   seconds=time.time() - t0)
        hist.append(row)
        print(f"epoch {ep}: train loss {row['train_loss']:.4f} · val MAE {row['val_mae']:.4f} · acc ±0.10 {row['val_acc10']:.3f} · {row['seconds']:.0f} s", flush=True)
        if wb is not None:
            wb.log(row)

        # 比之前最好的还好（至少好 0.00001）→ 存权重（只存被训练的部分，约 14 MB）；否则"没进步"计数 +1，到 patience 就停
        if np.isfinite(row["val_mae"]) and row["val_mae"] < best - 1e-5:
            best, bad = row["val_mae"], 0
            torch.save({"weights": N.trained_weights(model, names), "epoch": ep, "settings": settings}, ckpt)
        else:
            bad += 1
            if bad >= a.patience:
                print("early stopping", flush=True); break

    # 第 5 步：装回最好的一轮（不是最后一轮）的权重，重算验证集的预测和指标
    ck = N.load_weights(model, ckpt)
    model.to(device)
    P = predict(model, vl, device, use_amp, amp_dtype)
    T = metric_table(P, frames["val"], dims)
    S = summary(T)
    # config.json 的内容：设置、15 个目标的顺序（部署时按这个顺序读输出）、最好的是第几轮、用时、torch / timm 版本（部署时尽量一致）、验证集成绩
    cfg = dict(run_id=rid, settings=settings, targets=dims, best_epoch=ck["epoch"], epochs_run=len(hist), train_seconds=round(time.time() - t_start),
               device=str(device), versions=dict(torch=torch.__version__, timm=timm.__version__),
               created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), **S)

    # 写 5 个结果文件到机器本地的 rdir：
    #   best_model.pt（第 4 步已写）：部署用的权重 · history.parquet：每轮的损失和验证成绩 · metrics.parquet：15 个分数各自的成绩
    #   predictions.parquet：每个验证格子的 15 个预测（以后测 API 时拿来对照） · config.json：见上
    pd.DataFrame(hist).to_parquet(os.path.join(rdir, "history.parquet"), index=False)
    T.to_parquet(os.path.join(rdir, "metrics.parquet"), index=False)
    pd.DataFrame(P, columns=dims).assign(cell_id=frames["val"].cell_id.values).to_parquet(os.path.join(rdir, "predictions.parquet"), index=False)
    json.dump(cfg, open(os.path.join(rdir, "config.json"), "w"), indent=2, default=str)
    print(f"{rid} done in {cfg['train_seconds'] / 60:.1f} minutes · best epoch {cfg['best_epoch']} · val MAE {S['val_mae']:.4f} · acc ±0.10 {S['val_acc10']:.3f}", flush=True)
    if wb is not None:
        wb.summary.update(S | {"best_epoch": cfg["best_epoch"]})
        wb.finish()

    # 第 6 步：复制到 gs://<bucket>/results/<training-id>/{dry|final}/<run_id>/（Vertex 的机器用完就销毁，bucket 才是保存的地方）
    for f in sorted(os.listdir(rdir)):
        fs.put_file(os.path.join(rdir, f), f"{out_prefix}/{rid}/{f}")

    # latest.json：记下最新的是哪个 run，部署时读它就知道该用哪个 best_model.pt
    with fs.open(f"{out_prefix}/latest.json", "w") as f:
        json.dump({"run_id": rid, "training_id": a.training_id, "dry_run": a.dry_run, "updated_at": cfg["created_at"]}, f, indent=2)
    print(f"saved to {out_prefix}/{rid}/ and updated latest.json", flush=True)
    return rid


if __name__ == "__main__":
    main()
