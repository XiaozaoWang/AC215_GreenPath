#!/bin/bash
# 上云前用：按 Google 机器的架构（linux/amd64）build 镜像，推到 group project 的 Artifact Registry。
# Vertex 流程按镜像名去拉，所以改了代码、想让流程用新代码，就重新运行一次。
# 镜像里只有代码和依赖，没有任何 key 或 token。
#
# 第一次用之前：
#   gcloud artifacts repositories create greenpath --repository-format=docker --location=us-central1   # 整个项目建一次
#   gcloud auth configure-docker us-central1-docker.pkg.dev                                            # 每人一次：docker push 用你的 gcloud 登录

set -e

# 从 .env 只取项目名这一行（不读 token）
export $(grep '^GOOGLE_CLOUD_PROJECT=' "$(pwd)/../../.env")

export IMAGE_NAME="greenpath-data-labeler"
export IMAGE_URI="us-central1-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/greenpath/$IMAGE_NAME"

# amd64：Vertex 的机器是 amd64；Apple 芯片的 Mac 上是模拟 build，比 docker-shell.sh 慢
docker build --platform linux/amd64 -t $IMAGE_URI:latest --push -f Dockerfile .

echo "Pushed $IMAGE_URI:latest"
