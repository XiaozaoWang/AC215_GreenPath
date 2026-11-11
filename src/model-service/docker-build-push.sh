#!/bin/bash
# 上云前用：按 Google 机器的架构（linux/amd64）build 镜像，推到 Artifact Registry。之后在 model-deployment 运行 --deploy
# 换了模型（--prepare）或改了代码，都要重新运行一次。镜像里有模型，没有任何 key 或 token。
# 第一次用之前（整个项目一次、每人一次）的命令见 data-processor/docker-build-push.sh

set -e

# 从 .env 只取项目名这一行（不读 token）
export $(grep '^GOOGLE_CLOUD_PROJECT=' "$(pwd)/../../.env")

export IMAGE_NAME="greenpath-model-service"
export IMAGE_URI="us-central1-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/greenpath/$IMAGE_NAME"

# 没准备模型就停
if [ ! -f artifacts/best_model.pt ]; then
    echo "artifacts/ is empty: run 'python cli.py --prepare' in model-deployment first"
    exit 1
fi

# amd64：Cloud Run 的机器是 amd64；Apple 芯片的 Mac 上是模拟 build，比 docker-shell.sh 慢
docker build --platform linux/amd64 -t $IMAGE_URI:latest --push -f Dockerfile .

echo "Pushed $IMAGE_URI:latest"
