"""
Download one training set into greenpath_dataset/ so DVC can snapshot it (our version of the tutorial's gcsfuse mount).
Gets training/<training-id>/ (dataset.parquet, meta.json, exclusions.parquet) plus the aerial tile at every tile_path
in the table, keeping tile_path as is. Only reads the bucket; the only write to it is the dvc push afterwards.
Usage (in the container, under src/data-versioning/):
    python cli.py --training-id Yue_20261108_test_collector
"""
import argparse
import os
import shutil

import gcsfs
import pandas as pd

OUT = "greenpath_dataset"                                         # folder tracked by DVC (like cheese_dataset/ in the tutorial)
TABLES = ["dataset.parquet", "meta.json", "exclusions.parquet"]   # files to keep from training/<training-id>/; embeddings_*.parquet isn't used for training, so not kept


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--training-id", required=True, help="训练集名，读 training/<training-id>/")
    p.add_argument("--bucket", default=os.environ.get("GREENPATH_BUCKET"), help="bucket 名（默认用 .env 的 GREENPATH_BUCKET）")
    a = p.parse_args()
    fs = gcsfs.GCSFileSystem(token="google_default")              # uses the mounted gcloud login identity
    root = f"{a.bucket}/training/{a.training_id}"

    # Step 1: empty greenpath_dataset/. One folder holds one version only (same as the tutorial); the previous version is already in DVC, dvc pull gets it back
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)

    # Step 2: download the training table
    for name in TABLES:
        fs.get(f"{root}/{name}", f"{OUT}/{name}")
        print("table:", name)

    # Step 3: download the aerial tiles from the table's tile_path (all of train / val / test; handed to gcsfs in one call, it downloads in parallel)
    tiles = sorted(pd.read_parquet(f"{OUT}/dataset.parquet").tile_path.unique())
    for t in tiles:
        os.makedirs(os.path.dirname(f"{OUT}/{t}"), exist_ok=True)
    fs.get([f"{a.bucket}/{t}" for t in tiles], [f"{OUT}/{t}" for t in tiles])
    print(f"tiles: {len(tiles)}")

    # Next: dvc add greenpath_dataset && dvc push (see README)
    print(f"Done: {OUT}/ = training/{a.training_id}/ + {len(tiles)} tiles")


if __name__ == "__main__":
    main()
