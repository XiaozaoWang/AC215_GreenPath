#!/bin/bash
# First script that runs when the container starts: prints env info, activates the installed Python env, leaves you a shell
# The tutorial uses gcsfuse here to mount the bucket's images/ as cheese_dataset/; instead we run python cli.py in the shell to download

echo "Container is running!!!"
echo "Architecture: $(uname -m)"
echo "Python version: $(python --version)"
echo "UV version: $(uv --version)"
echo "Bucket: $GREENPATH_BUCKET"

# Activate the env installed at build time (dvc, gcsfs, pandas)
source /.venv/bin/activate

# Keep a shell open
exec /bin/bash
