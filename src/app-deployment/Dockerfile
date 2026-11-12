# 部署工具的镜像：只有 Python 和几个包（读 bucket、调 Cloud Run 的 API），在你电脑上运行，不上云
# 不放任何 key 或 .env，身份在运行时才交给容器

# 官方 Python 3.12（Debian）精简版
FROM python:3.12-slim-bookworm

# apt 不弹提示；用 UTF-8；日志不缓冲
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

# uv 把包装进 /.venv（在 /app 外面，挂载仓库时不会被盖掉）
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/.venv

# 装 uv；建一个普通用户 app，不用 root 运行（只用 Python 的现成安装包，不需要 apt 装系统软件）
RUN set -ex; \
    pip install --no-cache-dir uv && \
    useradd -ms /bin/bash app -d /home/app -u 1000 && \
    mkdir -p /app /.venv && \
    chown app:app /app /.venv

USER app
WORKDIR /app

# 先只复制依赖清单并安装：依赖没改时这一层用缓存，build 很快
COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --frozen

# 再复制代码（运行时 docker-shell.sh 会把整个仓库挂到 /app，用的是你电脑上最新的代码）
COPY --chown=app:app . ./

# 启动时先运行 docker-entrypoint.sh
ENTRYPOINT ["/bin/bash","docker-entrypoint.sh"]
