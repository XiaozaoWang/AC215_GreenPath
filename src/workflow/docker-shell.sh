#!/bin/bash
# 本地用：build 流程工具的镜像，进入容器终端，在里面运行 python cli.py --pipeline 等
#
# 身份（不进镜像，只在运行时交给容器）：
#   - 提交流程用你的 Google 账号：把 gcloud 登录凭证（ADC）只读挂进容器。你是 group project 的 Owner，
#     可以提交 Vertex 流程，也可以让流程以服务账号 greenpath-ml-workflow 的身份运行。不需要服务账号 key 文件。
#   - .env 里的 GOOGLE_CLOUD_PROJECT、GOOGLE_CLOUD_LOCATION、GREENPATH_BUCKET：用 `--env-file` 传进容器。
#   - 流程在云上运行时用 greenpath-ml-workflow 服务账号；Mapillary token 由采集步骤自己从 Secret Manager 取，这里用不到。

# 任何一步出错就停
set -e

export IMAGE_NAME="greenpath-workflow"
export BASE_DIR=$(pwd)
export ENV_FILE=$(pwd)/../../.env          # 仓库根目录的 .env（不进 git）

# build 镜像（本机架构）
docker build -t $IMAGE_NAME -f Dockerfile .

# 启动容器：代码目录挂到 /app（编译出的 yaml 也会出现在这里）；gcloud 凭证只读挂进去
docker run --rm --name $IMAGE_NAME -ti \
-v "$BASE_DIR":/app \
-v "$HOME/.config/gcloud":/home/app/.config/gcloud:ro \
--env-file "$ENV_FILE" \
$IMAGE_NAME "$@"
