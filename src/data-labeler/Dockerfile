# 镜像的“装修图纸”：从哪个系统开始、装哪些软件、把代码放进去、启动时做什么
# 镜像里只有系统、依赖和代码，不放任何 key、token 或 .env：身份和密钥都在运行时才交给容器

# 官方 Python 3.12（Debian）精简版
FROM python:3.12-slim-bookworm

ARG DEBIAN_PACKAGES="build-essential git curl wget unzip gzip"

# apt 不弹提示；用 UTF-8；日志不缓冲（Vertex 上能实时看到输出）
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

# uv 把包装进 /.venv
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
WORKDIR /app

# 先只复制依赖清单并安装：代码改了但依赖没改时，这一层直接用缓存，build 很快
COPY --chown=app:app pyproject.toml uv.lock* ./
RUN uv sync --frozen

# 再复制代码（.dockerignore 里列的本地缓存、检查图不会复制进来）
COPY --chown=app:app . ./

# matplotlib 的缓存放到可写的临时目录（家目录下的 .config 被挂进来的 gcloud 凭证占了）
ENV MPLCONFIGDIR=/tmp/matplotlib

# 启动时先运行 docker-entrypoint.sh
ENTRYPOINT ["/bin/bash","docker-entrypoint.sh"]
