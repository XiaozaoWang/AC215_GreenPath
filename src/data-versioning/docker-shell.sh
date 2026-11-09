#!/bin/bash
# 本地用：build 镜像，再启动容器，进入一个装好 DVC 的终端
#   sh docker-shell.sh
#
# 和 tutorial 的区别：
#   - 挂的是整个仓库（不只是这个文件夹）：DVC 要找到仓库根目录的 .git
#   - 不用服务账号 key：把你电脑上的 gcloud 登录凭证（ADC）只读挂进容器，读写 bucket 用你的 Google 账号
#   - 不挂 bucket（不需要 --privileged / fuse），bucket 名从 .env 读
#   - git commit / tag / push 在 Mac 上自己做，容器里只运行 python cli.py 和 dvc 命令

# 任何一步出错就停
set -e

export IMAGE_NAME="greenpath-data-versioning"
export REPO_DIR=$(cd ../.. && pwd)          # 仓库根目录 green-path-m3/
export ENV_FILE=$REPO_DIR/.env               # 仓库根目录的 .env（不进 git）

# build 镜像（本机架构）
docker build -t $IMAGE_NAME -f Dockerfile .

# 启动容器
#   整个仓库挂到 /app，从 /app/src/data-versioning 开始：下载的数据、.dvc 纸条直接出现在 Mac 的这个文件夹里
#   gcloud 凭证只读挂到容器用户的家目录，gcsfs 和 DVC 会自动找到它
docker run --rm --name $IMAGE_NAME -ti \
-v "$REPO_DIR":/app \
-w /app/src/data-versioning \
-v "$HOME/.config/gcloud":/home/app/.config/gcloud:ro \
--env-file "$ENV_FILE" \
$IMAGE_NAME
