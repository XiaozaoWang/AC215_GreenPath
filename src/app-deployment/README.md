# GreenPath app deployment

This directory follows `model-deployment`: a local Python SDK container with ADC,
`cli.py --deploy`, and `cli.py --test`. Only API and Frontend are deployment targets.
Image builds/pushes stay in each runtime directory, matching model-service.

```sh
# Host terminal: build/push API (this publishes an image).
cd src/api-service
sh docker-build-push.sh
# Another host terminal: enter the deployment container.
cd src/app-deployment
sh docker-shell.sh
# Inside the deployment container:
python cli.py --deploy --service api
python cli.py --test --service api --full
# Host terminal at repository root, after API passes:
cd src/frontend
sh docker-build-push.sh
# Inside the deployment container:
python cli.py --deploy --service frontend
python cli.py --test --service frontend --full
```

Without `--full`, tests check root, Boston membership, and API privacy. Full tests
make real Gemini/model calls. `--local` tests the existing Compose services instead.
Set `APP_IMAGE_TAG` when building and `--tag` when deploying to use a tag other than
`latest`. Registry authentication uses the same host Docker/gcloud setup as model-service.

`docker-shell.sh` mounts the repository and read-only gcloud ADC under the `app`
user's home; no Docker socket or gcloud executable is needed in the container.
`GCLOUD_CONFIG_DIR` can override `$HOME/.config/gcloud` (e.g. the gcloud directory
under AppData/Roaming on Windows). No credentials are built into images.

Project/region/bucket use the same .env names as model-deployment. API deployment
reads the existing Model Service URL and passes GCS/Gemini settings. Frontend
deployment reads the API URL. API is private and grants Invoker to the frontend
identity; Frontend is public. Model Service, RAG, ChromaDB and project IAM are unchanged.
Both services keep max 3 instances, request-based CPU, and port 8080. API concurrency
is 5 with a 900-second timeout; Frontend concurrency is 20 with a 1000-second timeout.
API model concurrency defaults to 3. RAG needs an existing reachable `RAG_SERVICE_URL`.
