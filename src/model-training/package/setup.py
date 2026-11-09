from setuptools import find_packages, setup

# Vertex AI 开机后先按这里装依赖，再运行 python -m trainer.task
# GPU 镜像（pytorch-gpu）里已经有 torch；CPU 镜像（sklearn-cpu）没有，由 pip 现装（启动慢几分钟）
REQUIRED_PACKAGES = ["torch", "timm", "gcsfs", "pyarrow", "wandb", "pillow"]

setup(
    name="greenpath-trainer",
    version="0.0.1",
    install_requires=REQUIRED_PACKAGES,
    packages=find_packages(),
    description="GreenPath Trainer Application",
)
