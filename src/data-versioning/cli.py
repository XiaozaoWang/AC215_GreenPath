"""
把一个训练集原样下载到 greenpath_dataset/，交给 DVC 拍快照。
相当于 tutorial 里 gcsfuse 把 images/ 挂成 cheese_dataset/ 那一步；我们的图散在共用的 cache/ 里，所以按训练表挑出来下载。

用法（在容器里，src/data-versioning/ 下）：
    python cli.py --training-id Yue_20261108_test_collector

从 bucket 下载到本地（只读 bucket，不在 bucket 里建任何东西；往 bucket 写的只有之后的 dvc push）：
    训练表（data-processor 写的）：gs://<bucket>/training/<training-id>/dataset.parquet、meta.json、exclusions.parquet
    航拍图（data-collector 写的）：gs://<bucket>/<tile_path>，tile_path 是训练表里的一列，每个格子一张图

本地结果：新建 src/data-versioning/greenpath_dataset/（不进 git），tile_path 原样保留，表里的路径在这个文件夹里照样能找到图：
    greenpath_dataset/
    ├── dataset.parquet、meta.json、exclusions.parquet
    └── cache/naip_tiles/154m_256px/<NAIP 图幅>/<h3>.png
"""
import argparse
import os
import shutil

import gcsfs
import pandas as pd

OUT = "greenpath_dataset"                                         # DVC 管的文件夹（相当于 tutorial 的 cheese_dataset/）
TABLES = ["dataset.parquet", "meta.json", "exclusions.parquet"]   # training/<training-id>/ 里要存的文件；embeddings_*.parquet 训练不用，不存


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--training-id", required=True, help="训练集名，读 training/<training-id>/")
    p.add_argument("--bucket", default=os.environ.get("GREENPATH_BUCKET"), help="bucket 名（默认用 .env 的 GREENPATH_BUCKET）")
    a = p.parse_args()
    fs = gcsfs.GCSFileSystem(token="google_default")              # 用挂进来的 gcloud 登录身份
    root = f"{a.bucket}/training/{a.training_id}"

    # 第 1 步：清空 greenpath_dataset/。一个文件夹只放一个版本（和 tutorial 一样），上一版已经在 DVC 里，删了能用 dvc pull 拿回来
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)

    # 第 2 步：下载训练表
    for name in TABLES:
        fs.get(f"{root}/{name}", f"{OUT}/{name}")
        print("table:", name)

    # 第 3 步：按训练表的 tile_path 下载航拍图（train / val / test 全部；一次交给 gcsfs，它会并行下载）
    tiles = sorted(pd.read_parquet(f"{OUT}/dataset.parquet").tile_path.unique())
    for t in tiles:
        os.makedirs(os.path.dirname(f"{OUT}/{t}"), exist_ok=True)
    fs.get([f"{a.bucket}/{t}" for t in tiles], [f"{OUT}/{t}" for t in tiles])
    print(f"tiles: {len(tiles)}")

    # 下一步：dvc add greenpath_dataset && dvc push（见 README）
    print(f"Done: {OUT}/ = training/{a.training_id}/ + {len(tiles)} tiles")


if __name__ == "__main__":
    main()
