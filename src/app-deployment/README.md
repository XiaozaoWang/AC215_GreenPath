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
discovers the existing Model and RAG Service URLs and passes GCS/Gemini settings. Frontend
deployment reads the API URL. API is private and grants Invoker to the frontend
identity; Frontend is public. Model Service, RAG, ChromaDB and project IAM are unchanged.
Both services keep max 3 instances, request-based CPU, and port 8080. API concurrency
is 5 with a 900-second timeout; Frontend concurrency is 20 with a 1000-second timeout.
API model concurrency defaults to 3. Both upstream services must already exist
in the configured project and region.

## Redeploy API with private RAG

The RAG handoff on `codex/rag-cloud-run` confirms that the API runtime account
`greenpath-api-service@organic-victory-508017-v9.iam.gserviceaccount.com`
already has Invoker access. The gateway reuses Model Service's ID-token helper
with the RAG base URL as audience. HTTPS uses the runtime identity; Compose keeps unauthenticated
`http://rag-api:8001`. No key files or IAM changes are needed.

Run these commands in Bash/Git Bash from the repository root:

```sh
cd src/api-service
sh docker-build-push.sh
cd ../app-deployment
sh docker-shell.sh
# Inside the deployment container:
python cli.py --deploy --service api
python cli.py --test --service api
exit
```

The CLI looks up `greenpath-rag-service` through Cloud Run and sets the API's
runtime `RAG_SERVICE_URL`, just as it discovers `greenpath-model-service` for
`MODEL_SERVICE_URL`. No RAG URL setting or export is needed in the local `.env`.
The existing frontend handler supplies an API-audience ID token for its private
API calls.

In the host terminal, verify the new API revision reaches RAG with its runtime
identity, then verify the frontend handler reaches the API:

```sh
PROJECT=organic-victory-508017-v9
REGION=us-central1
API_URL=$(gcloud run services describe greenpath-api-service --project "$PROJECT" --region "$REGION" --format='value(status.url)')
FRONTEND_URL=$(gcloud run services describe greenpath-frontend --project "$PROJECT" --region "$REGION" --format='value(status.url)')
TOKEN=$(gcloud auth print-identity-token)
curl --fail-with-body -sS --max-time 120 -H "Authorization: Bearer $TOKEN" "$API_URL/rag/ready"
curl --fail-with-body -sS --max-time 120 -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question":"What should I check before cycling?","n_results":2}' "$API_URL/rag/query"
curl --fail-with-body -sS --max-time 120 -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question":"What should I check before cycling?","n_results":5}' "$API_URL/rag/chat"
curl --fail-with-body -sS --max-time 120 -H 'Content-Type: application/json' \
  -d '{"question":"What should I check before cycling?"}' "$FRONTEND_URL/api/rag/chat"
unset TOKEN
```

Use an operator login with API Invoker access. Readiness should return
`ready=true` and `count=398` for the handoff corpus; query returns sources, and
both chat calls return a nonempty `answer` and `sources`. A cold start may need
a retry within the existing 5-second readiness / 50-second chat gateway limits.
Open the frontend, select **Ride Assistant**, submit the same question, and
check the answer and source links. In browser Network tools, the request should
be `POST /api/rag/chat` with HTTP 200. The complete path is:
Ride Assistant → DataService → Next.js Route Handler → private API `/rag/chat`
→ private RAG `/chat`. The browser never needs a Google token.
