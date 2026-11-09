# 镜像的“装修图纸”：系统 + git + DVC + 代码。不放任何 key 或 .env，身份在运行时才交给容器
# 和 tutorial 的区别：不装 gcsfuse，因为我们不挂载 bucket，而是用 cli.py 把要的文件下载下来

# 官方 Python 3.12（Debian）精简版
FROM python:3.12-slim-bookworm

# git：DVC 要在 git 仓库里工作（写 .dvc 纸条、.gitignore）
ARG DEBIAN_PACKAGES="build-essential git curl"

# apt 不弹提示；用 UTF-8；日志不缓冲
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

# uv 把包装进 /.venv（在 /app 外面，挂载代码时不会被盖掉）
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/.venv

# 装系统软件和 uv；建一个普通用户 app，不用 root 运行
RUN set -ex; \
    for i in $(seq 1 8); do mkdir -p "/usr/share/man/man${i}"; done && \
    apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends $DEBIAN_PACKAGES && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir --upgrade pip && \
    pip install uv && \
    useradd -ms /bin/bash app -d /home/app -u 1000 && \
    mkdir -p /app && \
    chown app:app /app && \
    mkdir -p /.venv && \
    chown app:app /.venv

USER app

# 仓库是从 Mac 挂进来的，文件主人不是容器里的 app，git 默认会拒绝操作；这里告诉 git 信任它
RUN git config --global --add safe.directory '*'

WORKDIR /app

# 先只复制依赖清单并安装：依赖没改时这一层用缓存，build 很快
COPY --chown=app:app pyproject.toml uv.lock* ./
RUN uv sync --frozen

# 再复制代码（.dockerignore 里列的数据、缓存不会复制进来）
COPY --chown=app:app . ./

# 启动时先运行 docker-entrypoint.sh
ENTRYPOINT ["/bin/bash","docker-entrypoint.sh"]
