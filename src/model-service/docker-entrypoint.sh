#!/bin/bash
# 容器启动后第一个运行的脚本，两种用法：
#   1. Cloud Run 和本地 sh docker-shell.sh（不带参数）：启动模型服务，监听 $PORT 端口（Cloud Run 给 8080；没有就用 8080）
#   2. sh docker-shell.sh bash（带参数）：不启动服务，运行传进来的命令，例如打开一个终端

echo "Container is running!!!"

# 启用 build 时装好的环境
source /.venv/bin/activate

if [[ $# -eq 0 ]];
then
    # 用法 1：0.0.0.0 = 接受容器外面来的请求；main:app = main.py 里的 app
    exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
else
    # 用法 2
    exec "$@"
fi
