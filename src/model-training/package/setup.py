from setuptools import find_packages, setup

# Vertex AI installs the dependencies from here first after the machine starts, then runs python -m trainer.task
# The GPU image (pytorch-gpu) already has torch; the CPU image (sklearn-cpu) doesn't, so pip installs it on the spot (startup is a few minutes slower)
REQUIRED_PACKAGES = ["torch", "timm", "gcsfs", "pyarrow", "wandb", "pillow"]

setup(
    name="greenpath-trainer",
    version="0.0.1",
    install_requires=REQUIRED_PACKAGES,
    packages=find_packages(),
    description="GreenPath Trainer Application",
)
