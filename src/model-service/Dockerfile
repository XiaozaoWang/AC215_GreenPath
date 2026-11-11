# 模型服务的镜像：部署到 Cloud Run，一直运行，收到请求就打分
# 镜像里有：系统 + Python 包 + 代码 + 模型（DINOv2 原厂权重 + artifacts/ 里我们训练的权重）
# 不放任何 key 或 .env：Cloud Run 上用服务挂的服务账号读 bucket，本地用挂进来的 gcloud 登录

# 官方 Python 3.12（Debian）精简版
FROM python:3.12-slim-bookworm

# apt 不弹提示；用 UTF-8；日志不缓冲（Cloud Run 上能实时看到输出）
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

# uv 把包装进 /.venv；DINOv2 原厂权重下载到 /hf（Hugging Face 的缓存目录）
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/.venv
ENV HF_HOME=/hf

# 装 uv；建一个普通用户 app，不用 root 运行（只用 Python 的现成安装包，不需要 apt 装系统软件）
RUN set -ex; \
    pip install --no-cache-dir uv && \
    useradd -ms /bin/bash app -d /home/app -u 1000 && \
    mkdir -p /app /.venv /hf && \
    chown app:app /app /.venv /hf

USER app
WORKDIR /app

# 先只复制依赖清单并安装：依赖没改时这一层用缓存，build 很快
# --no-cache：不在镜像里留 uv 的下载缓存（约 1 GB），镜像小，Cloud Run 冷启动拉镜像更快
COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache

# 把 DINOv2 原厂权重（约 90 MB）在 build 时下载进镜像：启动时不用再下载，冷启动更快，也不依赖 Hugging Face 网站
# 模型名从 network.py 读（和训练用的同一个），所以先只复制它
COPY --chown=app:app artifacts/network.py artifacts/
RUN /.venv/bin/python -c "import timm; from artifacts import network; timm.create_model(network.SOURCE, pretrained=True)"
ENV HF_HUB_OFFLINE=1

# 再复制代码和其余模型文件（best_model.pt、config.json）
COPY --chown=app:app . ./

# 启动时先运行 docker-entrypoint.sh
ENTRYPOINT ["/bin/bash","docker-entrypoint.sh"]
