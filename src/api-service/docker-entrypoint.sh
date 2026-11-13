#!/bin/bash

echo "Container is running!!!"

# Activate the uv virtual env (installed in /.venv when the image is built)
#the tutorial doesn't have this because it wasn't using uv back then, but I see ml2 requires uv, so it needs activating here
source /.venv/bin/activate

#wrote a branch at the end of the entrypoint, if DEV!=1 then go straight to uvicorn_server_production
#the difference between production and not here is, the dev version restarts the server by itself after files change and logs in a lot of detail, the production version has lifespan, which forces the startup/shutdown events to run and errors out if they aren't supported
uvicorn_server() {
    uvicorn api.service:app --host 0.0.0.0 --port "${PORT:-9000}" --log-level debug --reload --reload-dir api/ "$@"
    #uvicorn api.service:app --host 0.0.0.0 --port 9000 --log-level debug
}
# Cloud Run supplies PORT; exec lets Uvicorn receive shutdown signals directly.
uvicorn_server_production() {
    exec uvicorn api.service:app --host 0.0.0.0 --port "${PORT:-9000}" --lifespan on
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
