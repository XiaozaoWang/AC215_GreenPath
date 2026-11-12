#!/bin/bash
# 本地用：build 镜像，进容器终端，运行 python cli.py --deploy --service api / --deploy --service frontend / --test
#
# 身份：你电脑上的 gcloud 登录（ADC，由 gcloud auth application-default login 生成）只读挂进来，
#       读 bucket、调 Cloud Run 的 API 都用你的 Google 账号；不需要服务账号 key
# .env 里的变量（GREENPATH_BUCKET、GOOGLE_CLOUD_PROJECT）：用 --env-file 原样传进容器，不写进镜像

# 任何一步出错就停
set -e

export GCLOUD_CONFIG_DIR="${GCLOUD_CONFIG_DIR:-$HOME/.config/gcloud}"
export IMAGE_NAME="greenpath-app-deployment"
export REPO_DIR=$(cd ../.. && pwd)        # 仓库根目录
export ENV_FILE=$REPO_DIR/.env            # 仓库根目录的 .env（不进 git）

# build 镜像（本机架构）
docker build -t $IMAGE_NAME -f Dockerfile .

# 启动容器：仓库挂到 /app，从 src/app-deployment/ 开始；gcloud 凭证只读挂到容器用户的家目录，Google 的库会自动找到它
docker run --rm --name $IMAGE_NAME -ti \
-v "$REPO_DIR":/app \
-w /app/src/app-deployment \
-v "$GCLOUD_CONFIG_DIR":/home/app/.config/gcloud:ro \
--env-file "$ENV_FILE" \
$IMAGE_NAME
