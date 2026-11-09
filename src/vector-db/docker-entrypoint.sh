#!/bin/bash
# 容器启动后第一个运行的脚本，两种用法：
#   1. sh docker-shell.sh（不带参数）：打开一个终端，你自己运行 python cli.py ...
#   2. sh docker-shell.sh cli.py --chunk ...：直接运行这条命令，跑完容器就退出

echo "Container is running!!!"
echo "Architecture: $(uname -m)"

# 启用 build 时装好的环境
source /.venv/bin/activate

if [[ $# -eq 0 ]]; then
    exec /bin/bash
else
    # 用 "$@" 原样传参，--question "带空格的问题" 不会被拆开
    exec python "$@"
fi
