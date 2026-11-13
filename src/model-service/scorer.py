"""
The Model Service's "kitchen": at startup it gets the model and the cell index ready, then scores cells or images as they come in. main.py only takes requests and returns results.
At startup (on import, once, about 10-20 s): read artifacts/config.json (run_id, names and order of the 15 scores), build the model and load the trained weights
with the training network.py, then build the cell index from cells/*.parquet of each dataset in DATASETS (which aerial tile in the bucket belongs to each H3 cell).
The 3 files in artifacts/ (best_model.pt, config.json, network.py) are prepared by `python cli.py --prepare` in src/deployment and baked into the image at build time.
"""
import io
import json
import os

import gcsfs
import pandas as pd
import torch
from PIL import Image

from artifacts import network as N    # the same network.py used in training: build model, preprocess, load weights

BUCKET = os.environ["GREENPATH_BUCKET"]
DATASETS = os.environ["DATASETS"].split(",")      # areas that can be scored = cells in these datasets with downloaded aerial tiles, e.g. 1026_Full_data
BATCH_SIZE = 64                                   # how many images the model computes at once

# How many CPU threads to use: on Cloud Run --deploy sets it to the number of CPUs this instance gets; not set locally, so torch's default
if os.environ.get("TORCH_THREADS"):
    torch.set_num_threads(int(os.environ["TORCH_THREADS"]))

# Step 1: model info
CONFIG = json.load(open("artifacts/config.json"))
RUN_ID = CONFIG["run_id"]                         # e.g. dinov2_s-linear-seed42-ebcc811f
TRAINING_ID = CONFIG["settings"]["training_id"]   # which training set it was trained on, e.g. full_v2
TARGETS = CONFIG["targets"]                       # names of the 15 scores, order = the order of the model outputs

# Step 2: the model. First build the full DINOv2 + linear layer, then swap in the trained part; eval() = score only, no training
MODEL = N.build_model(len(TARGETS))
N.load_weights(MODEL, "artifacts/best_model.pt")
MODEL.eval()
TRANSFORM = N.eval_transform()                    # preprocessing: resize to 224x224, normalize

# Tool to read/write the bucket. Identity: locally = the mounted gcloud login; Cloud Run = the service account attached to the service
FS = gcsfs.GCSFileSystem(token="google_default")


# Step 3: cell index. Outputs one table: rows = H3 cells, columns = tile_path (path of the aerial tile in the bucket), tile_ok (can the image be used), tile_reason (why it can't)
def load_index():
    files = [f for d in DATASETS for f in FS.glob(f"{BUCKET}/datasets/{d}/cells/*.parquet")]   # one file per area
    assert files, f"no cells found in gs://{BUCKET}/datasets/{{{','.join(DATASETS)}}}/cells/"
    data = FS.cat(files)                                          # download these small files in parallel: {path: file bytes}
    cells = pd.concat(pd.read_parquet(io.BytesIO(b), columns=["h3_index", "tile_path", "tile_ok", "tile_reason"]) for b in data.values())
    # When the same cell is in two datasets, keep the image that can be used
    return cells.sort_values("tile_ok", ascending=False).drop_duplicates("h3_index").set_index("h3_index")


INDEX = load_index()
print(f"model {RUN_ID} ready · {len(TARGETS)} scores · {len(INDEX):,} cells from {DATASETS}", flush=True)


# A batch of images (raw bytes of PNG files), one set of scores per image {score name: 0-1}
# Same as in training: open image, convert to RGB, preprocess, model; 64 images at a time
@torch.inference_mode()                           # scoring only: no gradients, faster and less memory
def score_images(images):
    rows = []
    for i in range(0, len(images), BATCH_SIZE):
        x = torch.stack([TRANSFORM(Image.open(io.BytesIO(b)).convert("RGB")) for b in images[i:i + BATCH_SIZE]])
        rows += MODEL(x).tolist()
    return [dict(zip(TARGETS, (round(v, 4) for v in row))) for row in rows]


# A batch of H3 cells, one result per cell, same order as the request: {"h3_index", "status", "scores"}
# status: ok = scored; not_downloaded = not among the cells of DATASETS;
#         no_naip_coverage / mostly_black = the collector decided this cell's aerial tile can't be used (no NAIP / mostly black), not scored, scores is null
def score_cells(h3_indices):
    found = INDEX.reindex(h3_indices)                             # look up the index in request order; cells not found get an empty row
    usable = found.tile_ok.eq(True).to_numpy()                    # has an image, and the image can be used
    status = found.tile_reason.fillna("not_downloaded").where(~usable, "ok")
    paths = [f"{BUCKET}/{p}" for p in found.tile_path[usable]]
    images = FS.cat(paths)                                        # download these images from the bucket in parallel: {path: image bytes}
    scores = iter(score_images([images[p] for p in paths]))
    return [{"h3_index": h, "status": s, "scores": next(scores) if u else None}
            for h, s, u in zip(h3_indices, status, usable)]
