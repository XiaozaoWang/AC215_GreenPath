#!/bin/bash
# First script in the container: print some info, activate the Python env, leave a shell open for python cli.py ...

echo "Container is running!!!"
echo "Architecture: $(uname -m)"
echo "Python version: $(python --version)"
echo "Project: $GOOGLE_CLOUD_PROJECT · Bucket: $GREENPATH_BUCKET"

source /.venv/bin/activate

exec /bin/bash
