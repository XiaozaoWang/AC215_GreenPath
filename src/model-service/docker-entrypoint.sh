#!/bin/bash
# The first script that runs after the container starts, two ways to use it:
#   1. Cloud Run and local sh docker-shell.sh (no args): start the Model Service, listen on port $PORT (Cloud Run gives 8080; 8080 if not set)
#   2. sh docker-shell.sh bash (with args): don't start the service, run the passed command instead, e.g. open a terminal

echo "Container is running!!!"

# Turn on the environment installed at build time
source /.venv/bin/activate

if [[ $# -eq 0 ]];
then
    # Way 1: 0.0.0.0 = accept requests from outside the container; main:app = the app in main.py
    exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
else
    # Way 2
    exec "$@"
fi
