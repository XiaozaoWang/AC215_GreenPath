#!/bin/bash
# 本地用：build 镜像，再启动容器
#   sh docker-shell.sh                                     → 进入容器终端，自己运行 python cli.py ...
#   sh docker-shell.sh cli.py --dataset ... --areas ...    → 直接跑完就退出
#
# 身份和密钥（都不进镜像，只在运行时交给容器）：
#   - Google 身份：把你电脑上的 gcloud 登录凭证（ADC，由 gcloud auth application-default login 生成）只读挂进容器。
#     容器里读写 bucket、调 Gemini 都用你的 Google 账号，权限就是你在 group project 里的权限；不需要服务账号 key，也不需要 Gemini API key。
#   - .env 里的变量（bucket 名、GOOGLE_CLOUD_PROJECT = group project、GOOGLE_CLOUD_LOCATION）：用 `--env-file` 原样传进容器；
#     值不会出现在命令里，也不会写进镜像。打分用不到 Mapillary token。
#   - 云上（Vertex）没有你的电脑：身份换成任务指定的服务账号，项目由流程用环境变量 GOOGLE_CLOUD_PROJECT 传入。

# 任何一步出错就停
set -e

export IMAGE_NAME="greenpath-data-labeler"
export BASE_DIR=$(pwd)
export ENV_FILE=$(pwd)/../../.env          # 仓库根目录的 .env（不进 git）：GREENPATH_BUCKET、GOOGLE_CLOUD_PROJECT、GOOGLE_CLOUD_LOCATION

# build 镜像（本机架构，Apple 芯片的 Mac 上也很快）
docker build -t $IMAGE_NAME -f Dockerfile .

# 启动容器
#   代码目录挂到 /app：改了 cli.py 或 prompts/ 不用重新 build；检查图写到这里的 qc/
#   gcloud 凭证只读挂到容器用户的家目录，Google 的库会自动找到它
docker run --rm --name $IMAGE_NAME -ti \
-v "$BASE_DIR":/app \
-v "$HOME/.config/gcloud":/home/app/.config/gcloud:ro \
--env-file "$ENV_FILE" \
$IMAGE_NAME "$@"
