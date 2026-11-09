#!/bin/bash
# 本地用：
#   sh docker-shell.sh                          → 进入容器终端，自己运行 python cli.py ...
#   sh docker-shell.sh cli.py --chunk ...       → 直接跑完就退出
# 会自动先启动 chromadb 容器；用完运行 docker compose down 把它关掉（数据留在 docker-volumes/）
#
# 身份：~/secrets/llm-service-account.json（服务账号 key）只读挂进容器，只有 --embed / --query 会用到它
#       同学用自己的 key 和项目：把 key 放在 ~/secrets/ 同名，并改下面的 GCP_PROJECT

set -e

export GCP_PROJECT="ac215-507902"   # 生成向量用哪个项目（要和上面那个 key 属于同一个项目）

docker compose build
docker compose run --rm vector-db-cli "$@"
