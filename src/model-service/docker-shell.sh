#!/bin/bash
# 本地用：build 镜像，像在 Cloud Run 上一样启动模型服务，部署前先在自己电脑上试
#   sh docker-shell.sh          → 启动服务，浏览器打开 http://localhost:8080/docs 试用；Ctrl+C 停止
#   sh docker-shell.sh bash     → 不启动服务，进容器终端
#
# 先在 model-deployment 里运行 python cli.py --prepare，把模型放进 artifacts/
# 代码不挂进容器：跑的就是要部署的那个镜像，本地试通了，上云也一样
# 和 Cloud Run 上唯一的区别是身份：本地用你的 gcloud 登录（只读挂进来）读 bucket，云上用服务账号

# 任何一步出错就停
set -e

export IMAGE_NAME="greenpath-model-service"
export ENV_FILE=$(pwd)/../../.env          # 仓库根目录的 .env（不进 git），里面有 GREENPATH_BUCKET

# 能打分的区域（collector 采集过的数据集，逗号分隔）；云上的在 model-deployment/cli.py 的 DATASETS 里设
export DATASETS="${DATASETS:-1026_Full_data}"

# 没准备模型就停
if [ ! -f artifacts/best_model.pt ]; then
    echo "artifacts/ is empty: run 'python cli.py --prepare' in model-deployment first"
    exit 1
fi

# build 镜像（本机架构）
docker build -t $IMAGE_NAME -f Dockerfile .

# 启动容器：8080 端口接到你电脑的 8080；gcloud 凭证只读挂到容器用户的家目录，Google 的库会自动找到它
docker run --rm --name $IMAGE_NAME -ti \
-p 8080:8080 \
-v "$HOME/.config/gcloud":/home/app/.config/gcloud:ro \
--env-file "$ENV_FILE" \
-e DATASETS="$DATASETS" \
$IMAGE_NAME "$@"
