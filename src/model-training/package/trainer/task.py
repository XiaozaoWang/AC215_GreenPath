"""
GreenPath training (was the F2_train notebook): reads the training table made by data-processor and trains a model, one aerial tile in, 15 scores (0 to 1) out.
Runs on a temporary Vertex AI training machine (workflow/model.py submits the job, the machine runs python -m trainer.task). Usually not run locally.

1. dry run (default, CPU, 96 train + 48 val cells, 2 epochs, only checks the plumbing): python -m trainer.task --bucket <bucket> --training-id <id>
2. real training (all train + val, up to 25 epochs, needs an NVIDIA GPU): python -m trainer.task --bucket <bucket> --training-id <id> --no-dry-run
Reads gs://<bucket>/training/<training-id>/, writes gs://<bucket>/results/<training-id>/{dry|final}/<run_id>/ and latest.json (also logs to W&B if WANDB_API_KEY is set).
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

from trainer import network as N    # the network itself (build model, preprocessing, save/load weights) is in network.py, deployment uses it too

# Training settings: the final config picked after comparing in experiments (see the 1107-vertexAI handoff notes), no need to change
SEED = 42                    # random seed: same seed gives the same sampling, init and shuffle order every time, so results are reproducible
BATCH_SIZE = 16              # how many images go into the model at once (one batch), compute the error once, update params once
PATIENCE = 5                 # stop early if val MAE doesn't improve for 5 epochs in a row (early stopping), so it doesn't get worse
HEAD_LR = 3e-4               # learning rate of the linear layer: how big a step each update takes
ENCODER_LR = 1e-5            # learning rate of the unfrozen encoder layers: 30x smaller, just fine-tuning, don't break what pretraining learned
WEIGHT_DECAY = 1e-4          # weight decay: keeps params from getting too big, less overfitting
CLIP_NORM = 1.0              # gradient clipping: caps how big one update can be, so one crazy batch can't throw the model off
UNFREEZE_BLOCKS = 2          # unfreeze the last 2 transformer blocks of DINOv2 (the other 10 stay frozen)

DRY_CELLS = {"train": 96, "val": 48}       # how many cells per split in a dry run
TILE_DIR = "/tmp/greenpath/tiles"          # aerial tiles get copied from the bucket to this temp folder on the training machine first
RUNS_DIR = "/tmp/greenpath/runs"           # results are written on the machine first, then copied to the bucket at the end (the machine is destroyed after use)
TOLERANCES = {0.05: "acc05", 0.10: "acc10", 0.20: "acc20"}   # tolerance accuracy: share of predictions within 0.05 / 0.10 / 0.20 of the ground truth


# Define the 4 command line args
# On Vertex they come from the pipeline's training step (workflow/model.py): --bucket=... --training-id=... --dry-run
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="GreenPath training")

    # where to read: bucket name + training set name, together they make gs://<bucket>/training/<training-id>/
    p.add_argument("--bucket", default=os.environ.get("GREENPATH_BUCKET"), help="默认读环境变量 GREENPATH_BUCKET（Vertex 上没有，要传）")
    p.add_argument("--training-id", required=True, help="training/<training-id>/，data-processor 做出来的训练集")

    # how to run: dry run or real; max number of epochs
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True, help="默认 dry run；--no-dry-run = 正式训练（要 GPU）")
    p.add_argument("--epochs", type=int, default=25, help="最多几轮（dry run 固定 2 轮）")

    a = p.parse_args(argv)
    if not a.bucket:
        p.error("no bucket: pass --bucket or set GREENPATH_BUCKET")

    # dry run only runs 2 epochs, stops after 2 epochs with no improvement
    a.patience = PATIENCE
    if a.dry_run:
        a.epochs, a.patience = 2, 2
    return a


'''
main runs 6 steps in order (the first 5 happen locally on the training machine, only step 6 writes to the bucket):
Step 1 load_training_set: read the training table written by data-processor from the bucket, drop Test; take a small sample in a dry run
Step 2 download_tiles: copy the needed aerial tiles from the bucket to the machine (every epoch reads all images again, reading locally is much faster)
Step 3 build model and optimizer: network.build_model builds DINOv2-S + a linear layer, only the unfrozen parts get trained
Step 4 training loop: one epoch = one pass over all training images; after each epoch compute MAE on val, the best epoch is saved as best_model.pt
Step 5 recompute val with the best epoch, write the 5 result files
Step 6 copy to the bucket, update latest.json (tells deployment "which model is the latest")
'''


# ==================== Steps 1-2: read data ====================

# Step 1: read the training table. Input gs://<bucket>/training/<training-id>/ (dataset.parquet + meta.json written by data-processor)
# Output: the 15 target names, {"train": train cell table, "val": val cell table}, the version number of dataset.parquet
def load_training_set(fs, bucket, training_id, dry_run):
    root = f"gs://{bucket}/training/{training_id}"

    # the 15 target names come from meta.json (processor keeps them in columns.targets), not hard-coded
    dims = json.load(fs.open(f"{root}/meta.json"))["columns"]["targets"]
    # the training table has one row per cell; sorted by cell_id so the order is the same every time
    ds = pd.read_parquet(f"{root}/dataset.parquet").sort_values("cell_id").reset_index(drop=True)

    # check: no duplicate cells, at least train and val (a single-region test set has no test, that's ok), target scores all between 0 and 1
    assert ds.cell_id.is_unique and {"train", "val"} <= set(ds.split), "need unique cells and both train and val"
    assert ds[dims].min().min() >= 0 and ds[dims].max().max() <= 1, "scores must be between 0 and 1"

    # drop Test: training never sees the test cells, so the test results can be trusted
    ds = ds[ds.split != "test"].reset_index(drop=True)

    # dry run: take a small sample of train and val each (random_state=0: same sample every time)
    if dry_run:
        ds = pd.concat([g.sample(min(DRY_CELLS[s], len(g)), random_state=0) for s, g in ds.groupby("split")]).sort_values("cell_id").reset_index(drop=True)

    # split into two tables: train is for training, val is for scoring each epoch and picking the best one
    frames = {s: ds[ds.split == s].reset_index(drop=True) for s in ["train", "val"]}
    print(f"{training_id}: {len(dims)} scores · " + " · ".join(f"{s} {len(f):,}" for s, f in frames.items()) + (" (dry run subset)" if dry_run else ""), flush=True)

    # version number (generation): changes every time the file in the bucket is rewritten. Saved in config.json so later we can tell which version of the training table this model was trained on
    return dims, frames, fs.info(f"{root}/dataset.parquet")["generation"]


# Step 2: download aerial tiles. Input: tile_path in the training table (relative path in the bucket, e.g. cache/naip_tiles/154m_256px/.../8a2b...fff.png)
# Output: a small function local that turns a bucket path into the local path on the machine (the DataLoader uses it to read images)
def download_tiles(fs, bucket, paths):
    os.makedirs(TILE_DIR, exist_ok=True)
    local = lambda rel: os.path.join(TILE_DIR, os.path.basename(rel))    # the file name is the cell's H3 id, so no name clashes
    todo = [rel for rel in sorted(set(paths)) if not os.path.exists(local(rel))]   # skip the ones already downloaded
    print(f"{len(set(paths)):,} pictures needed, {len(todo):,} to download", flush=True)

    def fetch(rel):
        fs.get_file(f"gs://{bucket}/{rel}", local(rel) + ".part")
        os.replace(local(rel) + ".part", local(rel))                 # rename only when the download is done, so a broken download doesn't leave half an image

    # 16 threads download at the same time
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(fetch, todo))
    return local


# The "image fetcher" during training: the DataLoader asks it for image i, it reads the image, preprocesses it, hands back (image tensor, 15 target scores)
class Tiles(Dataset):

    def __init__(self, paths, y, transform, augment=False):
        # paths: local image paths; y: the 15 target scores of each image (missing ones are NaN); transform: the preprocessing from network.eval_transform; augment: random transforms or not
        self.paths, self.y, self.tf, self.augment = list(paths), np.asarray(y, dtype=np.float32), transform, augment

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, i):
        # read image, convert to RGB, preprocess (resize to 224x224, normalize)
        with Image.open(self.paths[i]) as im:
            x = self.tf(im.convert("RGB"))
        # data augmentation (training only): random left-right flip, up-down flip, rotate 0/90/180/270 degrees. An aerial tile is still the same piece of land however you turn it,
        # so this is like having several times more training images, and the model won't memorize "trees are top left"
        if self.augment:
            if random.random() < 0.5:
                x = x.flip(-1)
            if random.random() < 0.5:
                x = x.flip(-2)
            x = torch.rot90(x, random.randint(0, 3), (-2, -1))
        return x, torch.from_numpy(self.y[i].copy())


# ==================== Metrics (used in steps 4 and 5) ====================
# missing ground truth (NaN, e.g. cells without street view have no visual_clutter) is skipped, not counted as 0

# Input: P = the model's predictions (one row per cell, 15 columns), frame = the ground truth table for the same cells
# Output: put in rows, one row per dimension: how many ground truth values this dimension has, MAE (how far off on average, smaller is better), tolerance accuracy (bigger is better)
def metric_table(P, frame, dims):
    rows = []
    for j, d in enumerate(dims):
        y = frame[d].to_numpy(dtype=np.float64)
        ok = ~np.isnan(y)                     # only look at cells that have ground truth
        err = np.abs(y[ok] - P[ok, j])        # how far each cell's prediction is from the ground truth
        rows.append(dict(dimension=d, n=int(ok.sum()), mae=err.mean() if ok.any() else np.nan,
                         **{c: (err <= t + 1e-6).mean() if ok.any() else np.nan for t, c in TOLERANCES.items()}))
    return pd.DataFrame(rows)


# Take the above and average the 15 dimensions once more, what you get is "the overall score of this model": val_mae, val_acc05, val_acc10, val_acc20 (picking the best epoch only looks at val_mae)
def summary(T):
    return {f"val_{c}": float(T[c].mean()) for c in ["mae", *TOLERANCES.values()]}


# ==================== Steps 3-6: training ====================

# Let the model score a batch of images (no training, no param updates), returns 15 numbers between 0 and 1 per image
@torch.no_grad()
def predict(model, dl, device, use_amp, amp_dtype):
    model.eval()                              # switch to "exam mode"
    out = []
    for xb, _ in dl:
        with torch.autocast(device_type="cuda", dtype=amp_dtype, enabled=use_amp):
            out.append(model(xb.to(device, non_blocking=True)).float().cpu().numpy())
    return np.concatenate(out)


# Loss function: measures how bad a batch's predictions are, training is about making it smaller and smaller
# Uses Huber (smooth L1): small errors count as squared, big errors count linearly. The ground truth is scored by Gemini and is sometimes way off, Huber doesn't get dragged around by those points
# Only counts the dimensions of cells that have ground truth; returns 0 when the whole batch has no ground truth at all
def masked_loss(pred, target):
    ok = torch.isfinite(target)
    return nn.functional.smooth_l1_loss(pred[ok], target[ok], beta=0.1) if ok.any() else pred.sum() * 0


# Entry point: read args, then steps 1-6
def main(argv=None):
    a = parse_args(argv)
    fs = gcsfs.GCSFileSystem()                # tool to read/write the bucket; on Vertex the identity is the service account set in workflow/model.py
    out_prefix = f"gs://{a.bucket}/results/{a.training_id}/{'dry' if a.dry_run else 'final'}"   # where results go (dry run and real training are kept apart)

    # Device: use the GPU if there is an NVIDIA GPU, and turn on mixed precision (some math in 16-bit floats, faster and uses less GPU memory; T4 has no fast bfloat16, so float16)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    use_amp = device.type == "cuda"
    amp_dtype = torch.bfloat16 if (use_amp and torch.cuda.get_device_capability()[0] >= 8) else torch.float16
    print("Device:", device, flush=True)

    # Real training but no GPU: stop right away (one epoch on CPU takes hours, wasted money)
    if not use_amp and not a.dry_run:
        raise RuntimeError("No NVIDIA GPU found. A real run needs a GPU; use --dry-run to check the plumbing on a CPU.")
    num_workers = 2 if (sys.platform == "linux" and not a.dry_run) else 0    # in real training use 2 subprocesses to read images, so the GPU doesn't wait

    # Steps 1-2: read the training set, download images; preprocessing is the same one deployment uses (network.eval_transform)
    dims, frames, data_version = load_training_set(fs, a.bucket, a.training_id, a.dry_run)
    local = download_tiles(fs, a.bucket, list(frames["train"].tile_path) + list(frames["val"].tile_path))
    tf = N.eval_transform()

    # Turn a cell table into a DataLoader: hands out one batch each time (16 images + 16 sets of target scores) (say I have 8 batches in total and epoch is 2, then it gets fed 16 times in total)
    # train: shuffled every epoch + augmentation; val: fixed order, no augmentation (each epoch takes the exam on the same questions, so the scores can be compared)
    def loader(split):
        augment = split == "train"
        g = torch.Generator()
        g.manual_seed(SEED)                   # shuffling also uses a fixed seed, so it's reproducible
        f = frames[split]
        return DataLoader(Tiles([local(p) for p in f.tile_path], f[dims].to_numpy(dtype=np.float32), tf, augment=augment), batch_size=BATCH_SIZE, shuffle=augment,
                          num_workers=num_workers, pin_memory=use_amp, generator=g if augment else None)

    # All the settings of this run: written into config.json and best_model.pt, so later we can see how the model was trained
    # run_id = hash of the settings (same keys as the notebook), same settings give the same name, e.g. dinov2_s-linear-seed42-ebcc811f
    settings = dict(training_id=a.training_id, data=data_version, backbone=N.BACKBONE, source=N.SOURCE,
                    unfrozen=f"last {UNFREEZE_BLOCKS} transformer blocks + final norm", head="linear", seed=SEED,
                    batch_size=BATCH_SIZE, epochs=a.epochs, patience=a.patience, head_lr=HEAD_LR, encoder_lr=ENCODER_LR, weight_decay=WEIGHT_DECAY, clip_norm=CLIP_NORM,
                    precision=str(amp_dtype).replace("torch.", "") if use_amp else "none", preprocessing=N.PREPROCESSING, dry_run=a.dry_run)
    rid = f"{N.BACKBONE}-linear-seed{SEED}-{hashlib.md5(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:8]}" #name based on the recipe card, rid = "dinov2_s-linear-seed42-" + first 8 chars of the recipe card's "fingerprint"
    rdir = os.path.join(RUNS_DIR, rid) 
    os.makedirs(rdir, exist_ok=True) #make a folder on the training machine, the 5 files like best_model.pt get written here first during training.
    print(f"run: {rid}", flush=True)

    # Step 3: build model and optimizer
    # First fix all random seeds (Python, numpy, PyTorch), so the linear layer's starting values and the augmentation are the same every time
    random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
    if use_amp:
        torch.cuda.manual_seed_all(SEED)
    model = N.build_model(len(dims), UNFREEZE_BLOCKS).to(device)
    
    # Optimizer AdamW: updates params using the gradient of the loss. Two param groups, two learning rates: unfrozen encoder layers fine-tune in small steps, the linear layer learns in big steps; frozen layers are not put in, so they never change
    opt = torch.optim.AdamW([{"params": [p for p in model.encoder.parameters() if p.requires_grad], "lr": ENCODER_LR},
                             {"params": model.head.parameters(), "lr": HEAD_LR}], weight_decay=WEIGHT_DECAY)
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp and amp_dtype == torch.float16)   # with float16, scale up the loss so tiny gradients don't turn into 0; does nothing on CPU
    tl, vl = loader("train"), loader("val")
    names = N.trainable_names(model)          # names of the trained params, only these get saved into best_model.pt

    # W&B: log the training curves only if there is a key (viewable on the web), otherwise train as usual
    wb, key = None, os.environ.get("WANDB_API_KEY")
    if key:
        import wandb
        wandb.login(key=key)
        group = "dry" if a.dry_run else "final"
        wb = wandb.init(project="greenpath", group=group, name=rid, tags=[group, a.training_id], config=settings)
    else:
        print("no WANDB_API_KEY: this run is NOT logged to Weights & Biases")

    # Step 4: training loop. One epoch = one pass over all training images in batches: predict, compute loss, compute gradients, update params
    # After each epoch take one exam on val: if MAE beats the best so far, save this epoch's weights as best_model.pt; stop early after patience epochs in a row with no progress
    ckpt, best, bad, hist, t_start = os.path.join(rdir, "best_model.pt"), float("inf"), 0, [], time.time()
    for ep in range(1, a.epochs + 1):
        model.train()                         # switch to "training mode"
        t0, loss_sum, steps = time.time(), 0.0, 0
        for xb, yb in tl:
            xb, yb = xb.to(device, non_blocking=True), yb.to(device, non_blocking=True)
            opt.zero_grad(set_to_none=True)   # clear the gradients from the last step
            with torch.autocast(device_type="cuda", dtype=amp_dtype, enabled=use_amp):
                out = model(xb)               # predict: 16 images x 15 scores
            loss = masked_loss(out.float(), yb)
            scaler.scale(loss).backward(); scaler.unscale_(opt)   # backprop: work out which way each param should move
            nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], CLIP_NORM)
            scaler.step(opt); scaler.update()                     # update params using the gradients
            loss_sum += float(loss.detach()); steps += 1

        # This epoch's results: training loss + val metrics, added to history (written to history.parquet at the end)
        row = dict(epoch=ep, train_loss=loss_sum / max(steps, 1), **summary(metric_table(predict(model, vl, device, use_amp, amp_dtype), frames["val"], dims)),
                   seconds=time.time() - t0)
        hist.append(row)
        print(f"epoch {ep}: train loss {row['train_loss']:.4f} · val MAE {row['val_mae']:.4f} · acc ±0.10 {row['val_acc10']:.3f} · {row['seconds']:.0f} s", flush=True)
        if wb is not None:
            wb.log(row)

        # Better than the best so far (by at least 0.00001), then save the weights (only the trained part, about 14 MB); otherwise "no progress" count +1, stop when it reaches patience
        if np.isfinite(row["val_mae"]) and row["val_mae"] < best - 1e-5:
            best, bad = row["val_mae"], 0
            torch.save({"weights": N.trained_weights(model, names), "epoch": ep, "settings": settings}, ckpt)
        else:
            bad += 1
            if bad >= a.patience:
                print("early stopping", flush=True); break

    # Step 5: load back the weights of the best epoch (not the last one), recompute the val predictions and metrics
    ck = N.load_weights(model, ckpt)
    model.to(device)
    P = predict(model, vl, device, use_amp, amp_dtype)
    T = metric_table(P, frames["val"], dims)
    S = summary(T)
    # What goes into config.json: settings, order of the 15 targets (deployment reads the outputs in this order), which epoch was best, time taken, torch / timm versions (keep them close at deployment), val results
    cfg = dict(run_id=rid, settings=settings, targets=dims, best_epoch=ck["epoch"], epochs_run=len(hist), train_seconds=round(time.time() - t_start),
               device=str(device), versions=dict(torch=torch.__version__, timm=timm.__version__),
               created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), **S)

    # Write the 5 result files to rdir on the machine:
    #   best_model.pt (already written in step 4): weights for deployment; history.parquet: loss and val results per epoch; metrics.parquet: results for each of the 15 scores
    #   predictions.parquet: the 15 predictions for each val cell (to compare against when testing the API later); config.json: see above
    pd.DataFrame(hist).to_parquet(os.path.join(rdir, "history.parquet"), index=False)
    T.to_parquet(os.path.join(rdir, "metrics.parquet"), index=False)
    pd.DataFrame(P, columns=dims).assign(cell_id=frames["val"].cell_id.values).to_parquet(os.path.join(rdir, "predictions.parquet"), index=False)
    json.dump(cfg, open(os.path.join(rdir, "config.json"), "w"), indent=2, default=str)
    print(f"{rid} done in {cfg['train_seconds'] / 60:.1f} minutes · best epoch {cfg['best_epoch']} · val MAE {S['val_mae']:.4f} · acc ±0.10 {S['val_acc10']:.3f}", flush=True)
    if wb is not None:
        wb.summary.update(S | {"best_epoch": cfg["best_epoch"]})
        wb.finish()

    # Step 6: copy to gs://<bucket>/results/<training-id>/{dry|final}/<run_id>/ (Vertex machines are destroyed after use, the bucket is where things are kept)
    for f in sorted(os.listdir(rdir)):
        fs.put_file(os.path.join(rdir, f), f"{out_prefix}/{rid}/{f}")

    # latest.json: records which run is the latest, deployment reads it to know which best_model.pt to use
    with fs.open(f"{out_prefix}/latest.json", "w") as f:
        json.dump({"run_id": rid, "training_id": a.training_id, "dry_run": a.dry_run, "updated_at": cfg["created_at"]}, f, indent=2)
    print(f"saved to {out_prefix}/{rid}/ and updated latest.json", flush=True)
    return rid


if __name__ == "__main__":
    main()
