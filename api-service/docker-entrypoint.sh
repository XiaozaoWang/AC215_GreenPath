#!/bin/bash

echo "Container is running!!!"

# Activate the same external uv environment used by all Python containers.
source /opt/venv/bin/activate

#There's a branch at the end of the entrypoint: if DEV!=1, run uvicorn_server_production directly
#The difference between production and not: the dev version restarts the server automatically after files change and prints very detailed logs; the production version has lifespan, which forces the startup/shutdown events to run and fails right away if they're not supported
uvicorn_server() {
    uvicorn api.service:app --host 0.0.0.0 --port 9000 --log-level debug --reload --reload-dir api/ "$@"
    #uvicorn api.service:app --host 0.0.0.0 --port 9000 --log-level debug
}
uvicorn_server_production() {
    uvicorn api.service:app --host 0.0.0.0 --port 9000 --lifespan on
}


export -f uvicorn_server
export -f uvicorn_server_production

echo -en "\033[92m
The following commands are available:
    uvicorn_server
        Run the Uvicorn Server
\033[0m
"

if [ "${DEV}" = 1 ]; then
  exec /bin/bash
else
  uvicorn_server_production
fi
