#!/bin/bash
# 容器启动后第一个运行的脚本：打印环境信息，启用装好的 Python 环境，留一个终端给你
# tutorial 在这里用 gcsfuse 把 bucket 的 images/ 挂成 cheese_dataset/；我们改成在终端里运行 python cli.py 下载

echo "Container is running!!!"
echo "Architecture: $(uname -m)"
echo "Python version: $(python --version)"
echo "UV version: $(uv --version)"
echo "Bucket: $GREENPATH_BUCKET"

# 启用 build 时装好的环境（dvc、gcsfs、pandas）
source /.venv/bin/activate

# 留一个终端
exec /bin/bash
