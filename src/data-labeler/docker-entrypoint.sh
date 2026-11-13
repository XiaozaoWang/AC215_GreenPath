#!/bin/bash
# First script that runs after the container starts. Two ways to use it:
#   1. Local (docker-shell.sh with no args): opens a shell, you run python cli.py ... yourself
#   2. A step in the Vertex pipeline (or docker-shell.sh with args): runs the cli.py command passed in, then the container exits

echo "Container is running!!!"
echo "Architecture: $(uname -m)"
echo "Python version: $(python --version)"
echo "UV version: $(uv --version)"

args="$@"
echo $args

# activate the env installed at build time
source /.venv/bin/activate

if [[ -z ${args} ]];
then
    # use 1: no args, keep a shell open
    exec /bin/bash
else
    # use 2: run the command passed in, e.g. "cli.py --dataset 1026_Full_data --areas all"
    uv run python $args
fi
