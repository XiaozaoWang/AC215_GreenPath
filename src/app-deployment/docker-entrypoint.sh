#!/bin/bash
# 容器启动后第一个运行的脚本：打印环境信息，启用装好的 Python 环境，留一个终端给你运行 python cli.py ...

echo "Container is running!!!"
echo "Architecture: $(uname -m)"
echo "Python version: $(python --version)"
echo "Project: $GOOGLE_CLOUD_PROJECT · Bucket: $GREENPATH_BUCKET"

# 启用 build 时装好的环境
source /.venv/bin/activate

# 留一个终端
exec /bin/bash
