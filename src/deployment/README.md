# Deployment

One tool for the three Cloud Run services we deploy: `model` (model-service), `api` (api-service), `frontend`.
RAG is deployed separately (`src/rag-service/deploy`). Settings for each service are in the `SERVICES` table in `cli.py`.

| service | who can call it | instances | requests per instance | CPU / memory |
|---|---|---|---|---|
| model | api-service only | 0-100 | 1 | 4 / 4 GiB |
| api | frontend only | 0-1 | 20 | 2 / 2 GiB |
| frontend | everyone | 0-3 | 20 | 1 / 512 MiB |

The API sends at most `--model-concurrency` (default 100) model calls at once, so model-service needs at least that many instances. `--deploy --service api` refuses to deploy if it does not fit, so deploy the model first.

```sh
# Mac: push the image of the service you changed
cd src/model-service && sh docker-build-push.sh        # or src/api-service, src/frontend
# Mac: open the tool
cd src/deployment && sh docker-shell.sh
# in the container
python cli.py --prepare                          # model only: copy the trained model into model-service/artifacts/ (before pushing)
python cli.py --deploy --service model
python cli.py --test --service model             # 50 validation cells must match training (--local: local container)
python cli.py --deploy --service api && python cli.py --test --service api --full
python cli.py --deploy --service frontend && python cli.py --test --service frontend
```

First time only: enable the Cloud Run Admin API, create the service accounts `greenpath-model-service` (Storage Object Viewer on the bucket), `greenpath-api-service` (Vertex AI User, Storage Object Viewer) and `greenpath-frontend`, and check that the Cloud Run CPU quota in us-central1 is at least 400 vCPU (100 model instances x 4).
