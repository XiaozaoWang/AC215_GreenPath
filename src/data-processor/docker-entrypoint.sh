#!/bin/bash
# First script that runs when the container starts. Two ways to use it:
#   1. Local (docker-shell.sh with no args): opens a shell, you run python cli.py ... yourself
#   2. A step in the Vertex pipeline (or docker-shell.sh with args): runs the given cli.py command directly, the container exits when it's done

echo "Container is running!!!"
echo "Architecture: $(uname -m)"
echo "Python version: $(python --version)"
echo "UV version: $(uv --version)"

args="$@"
echo $args

# Activate the env installed at build time
source /.venv/bin/activate

if [[ -z ${args} ]];
then
    # Usage 1: no args, keep a shell open
    exec /bin/bash
else
    # Usage 2: run the given command, e.g. "cli.py --prepare --output-dir gs://.../training/<new id>"
    uv run python $args
fi
