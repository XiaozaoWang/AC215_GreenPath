#!/bin/bash
# 容器启动后第一个运行的脚本，两种用法：
#   1. 本地（docker-shell.sh 不带参数）：打开一个终端，你自己运行 python cli.py ...
#   2. Vertex 流程的一步（或 docker-shell.sh 带参数）：直接运行传进来的 cli.py 命令，跑完容器就退出

echo "Container is running!!!"
echo "Architecture: $(uname -m)"
echo "Python version: $(python --version)"
echo "UV version: $(uv --version)"

args="$@"
echo $args

# 启用 build 时装好的环境
source /.venv/bin/activate

if [[ -z ${args} ]];
then
    # 用法 1：没有参数，留一个终端
    exec /bin/bash
else
    # 用法 2：运行传进来的命令，例如 "cli.py --pipeline"
    uv run python $args
fi
