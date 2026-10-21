# API Service

FastAPI serves saved vision predictions on port 9000. The request flow is
`preference → agent harness → weights → saved cell scores → weighted GeoJSON`.
The harness validates structured replies, retries up to three times and falls back to
zero weights on failure. The default provider is an offline keyword parser; Gemini is optional.

## Run and inspect

Run all commands from the repository root, after creating `.env` from `.env.example`:

```sh
docker compose up --build -d --wait api-service
```

Open http://localhost:9000/docs. `/preview` returns a saved score view without preference
parsing; `/score` accepts preference text and an optional result version. `/capabilities`
describes supported dimensions; `/versions` lists saved results. The API does not run CLIP
or Ridge on requests. Stop with `docker compose down`.

## Using the real Gemini


The default keyword stand-in only recognizes fixed keywords. For broader natural-language
understanding, use Gemini with an authorized project and billing enabled.

Add to `.env`:

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.gcp.yml
LLM_PROVIDER=gemini
```

**Option 1: team project (recommended).** Requires Vertex AI access on the team project;
usage is billed to that project. If `ADC_FILE` is set in `.env`, it must point to your
valid credentials; otherwise Compose uses the default local ADC file.

```sh
gcloud auth application-default login
gcloud auth application-default set-quota-project organic-victory-508017-v9
docker compose up --build
```

**Option 2: your own project.** Enable Vertex AI and billing on your project, and set:

```dotenv
GCP_PROJECT=your-project-id
# Optional: only set this when using an existing authorized credential file.
# ADC_FILE=/full/path/to/your-service-account-key.json
```

Without a credential file, leave `ADC_FILE` unset and authenticate locally:

```sh
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
docker compose up --build
```

Never commit `.env` or credentials to Git.

## Backend modules


| File | Role |
| --- | --- |
| `agent.py` | Asks Gemini and checks the format; retries invalid replies and falls back to all-zero weights after three failures. |
| `llm.py` | Calls Gemini; `LLM_PROVIDER` switches between Gemini and the stand-in. |
| `prompts.py` | Gemini instructions: dimensions and weighting rules. |
| `fake_llm.py` | Offline keyword stand-in; no key needed. |
| `schemas.py` | Data format definitions and validation. |
| `dimensions.py` | Reads the dimension table. |
| `tiles.py` | Returns the cells for a region. |
| `model_client.py` | Reads per-cell scores and checks one-to-one cell alignment. |
| `score_math.py` | Standardizes scores, clips extremes, applies weights and must-have thresholds. |
| `pipeline.py` | Runs the modules in order. |
| `service.py` | FastAPI entry point and endpoints. |
| `routers/score.py` | Receives preference/scoring requests and returns results. |
| `routers/source.py` | Read-only backend source viewer endpoint. |
| `model_bundle.py`, `versions.py`, `artifacts.py` | Load bundles, resolve versions, and provide GCS transfer utilities. |

## Result versions


A fresh checkout shows the bundled 188 cells. After `make run` trains a model, the new
result becomes the default. To return to the bundled result:

```sh
make app-versions APP_ARGS="--version boston-heldout-en-v2"
```

## FAQ


| Symptom | Cause / fix |
| --- | --- |
| The map does not change after input | Try the example keywords; the stand-in does not understand every phrasing. With Gemini, check logs for `LLM call failed`. |
| Credentials not found | Check that `docker-compose.gcp.yml` is included through `COMPOSE_FILE`, and that the ADC file exists and is mounted. |
| Port already in use | Stop the containers owning the port. For this app, run `docker compose down` before restarting. |

## Environment

The Python 3.12 image installs dependencies from `pyproject.toml` and `uv.lock` into
`/opt/venv`; `docker-entrypoint.sh` starts `api.service:app` through Uvicorn. The source
bind mount does not hide the environment. See [dependencies](../../docs/DEPENDENCIES.md),
[pipeline](../../docs/ML_PIPELINE.md) and [versioning](../../docs/DATA_AND_MODEL_VERSIONING.md).
