#!/bin/sh
# 把训练代码 package/ 打包成 trainer.tar.gz，上传到 gs://<bucket>/trainer/greenpath-trainer.tar.gz
# 流程的训练步骤（workflow/model.py）在 Vertex 上开训练机器时，从这里取代码；所以改了训练代码要重新运行一次
# 在 Mac 上直接运行（要先装好、登录好 gcloud）：sh package-trainer.sh
set -e
cd "$(dirname "$0")"

# 从仓库根目录的 .env 只取 bucket 这一行（不读 token）
GCS_BUCKET_NAME=$(grep -E '^GREENPATH_BUCKET=' ../../.env | cut -d= -f2- | tr -d "\"' ")

rm -f trainer.tar trainer.tar.gz
COPYFILE_DISABLE=1 tar --exclude='__pycache__' --exclude='*.egg-info' --exclude='.DS_Store' -cvf trainer.tar package
gzip trainer.tar
gcloud storage cp trainer.tar.gz "gs://$GCS_BUCKET_NAME/trainer/greenpath-trainer.tar.gz"
