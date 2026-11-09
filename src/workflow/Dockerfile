# 流程工具的镜像：只装 Vertex AI 和 kfp，用来定义、编译、提交流程（和 tutorial 的 workflow 一样只在本地用，不作为流程的一步）
# 不装 gcloud：身份用挂进来的 gcloud 登录凭证（ADC），不用服务账号 key

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

# 启动时先运行 docker-entrypoint.sh
ENTRYPOINT ["/bin/bash","docker-entrypoint.sh"]
