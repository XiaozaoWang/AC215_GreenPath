GCS_SERVING_URI ?= gs://ac215-greenpath/serving/boston-heldout-en-v2

.PHONY: app fetch-serving verify-serving app-gcs test
app:
	docker compose up --build

fetch-serving:
	cd api-service && uv run --frozen --python 3.12 python -m api.artifacts fetch $(GCS_SERVING_URI) ../.artifacts/boston-heldout-en-v2

verify-serving:
	cd api-service && uv run --frozen --python 3.12 python -m api.artifacts verify ../.artifacts/boston-heldout-en-v2

app-gcs: fetch-serving
	MODEL_BUNDLE_DIR=/artifacts/boston-heldout-en-v2 docker compose up --build

test:
	cd api-service && uv run --frozen --python 3.12 pytest -q

# Step 2 components run in separate environments. These are not the complete ML pipeline.
DATA_ROOT ?= $(GREENPATH_ROOT)
MODEL_VERSION ?= m1-clip-ridge-v1
MODEL_DIR ?= $(CURDIR)/.artifacts/models/$(MODEL_VERSION)
ENCODER_DIR ?= $(CURDIR)/.artifacts/encoders/clip-b16-laion2b-v1
TRAIN_CONFIG ?= $(DATA_ROOT)/results/baselines-first-run-20261013T0242/config.json
IMAGE_INPUT ?= $(CURDIR)/.artifacts/samples/boston-image-smoke-v1/inputs.json
IMAGE_ROOT ?= $(CURDIR)/.artifacts/samples/boston-image-smoke-v1
SMOKE_VERSION ?= boston-image-smoke
EMBEDDING_OUTPUT ?= $(CURDIR)/.artifacts/embeddings/$(SMOKE_VERSION)-embeddings-v1
PREDICTION_OUTPUT ?= $(CURDIR)/.artifacts/predictions/$(SMOKE_VERSION)-predictions-v1

.PHONY: ml-test train-m1 predict-images
ml-test:
	cd src/model-training && uv run --frozen --python 3.11 pytest -q
	cd src/feature-encoder && uv run --frozen --python 3.11 pytest -q

train-m1:
	@test -n "$(DATA_ROOT)" || (echo "Set DATA_ROOT or GREENPATH_ROOT to the existing research data directory."; exit 1)
	cd src/model-training && uv run --frozen --python 3.11 greenpath-train --data-root "$(DATA_ROOT)" --config "$(TRAIN_CONFIG)" --contract "$(CURDIR)/api-service/fixtures/boston-heldout-en-v2/schema.json" --encoder-dir "$(ENCODER_DIR)" --output "$(MODEL_DIR)" --artifact-version "$(MODEL_VERSION)"

predict-images:
	cd src/feature-encoder && uv run --frozen --python 3.11 greenpath-encode encode --encoder-dir "$(ENCODER_DIR)" --input "$(IMAGE_INPUT)" --data-root "$(IMAGE_ROOT)" --output "$(EMBEDDING_OUTPUT)" --artifact-version "$(SMOKE_VERSION)-embeddings-v1" --device cpu --batch-size 3
	cd src/model-deploy && uv run --frozen --python 3.11 greenpath-predict --model-dir "$(MODEL_DIR)" --embeddings "$(EMBEDDING_OUTPUT)/embeddings.parquet" --output "$(PREDICTION_OUTPUT)" --artifact-version "$(SMOKE_VERSION)-predictions-v1"

PIPELINE_MODE ?= smoke
PIPELINE_ARGS ?=
.PHONY: run pipeline-test
run:
	python3 tools/run_pipeline.py --mode "$(PIPELINE_MODE)" $(PIPELINE_ARGS)

pipeline-test: ml-test
	cd src/data-processor && uv run --frozen --python 3.11 pytest -q
	python3 -m unittest discover -s tools/tests -v

# Explicit upstream jobs. None of these targets is called by make run.
SOURCE_MAX_IMAGES ?= 8
SOURCE_COMPOSE = docker compose -p greenpath-sources -f docker-compose.sources.yml
.PHONY: source-build collect-streetview collect-tiles collect-features label-images label-report
source-build:
	$(SOURCE_COMPOSE) build

# Run 01-04 inside one container; SOURCE_ARGS controls explicit paid labeling.
SOURCE_ARGS ?=
.PHONY: collect-sources
collect-sources:
	LOCAL_UID="$$(id -u)" LOCAL_GID="$$(id -g)" ADC_FILE="$${ADC_FILE:-$$HOME/.config/gcloud/application_default_credentials.json}" $(SOURCE_COMPOSE) -f docker-compose.sources.gcp.yml run --rm --build data-source all --config /config/source.json --data-root /data $(SOURCE_ARGS)

collect-streetview:
	LOCAL_UID="$$(id -u)" LOCAL_GID="$$(id -g)" $(SOURCE_COMPOSE) run --rm --build data-source streetview --config /config/source.json --data-root /data

collect-tiles:
	LOCAL_UID="$$(id -u)" LOCAL_GID="$$(id -g)" $(SOURCE_COMPOSE) run --rm --build data-source tiles --config /config/source.json --data-root /data

collect-features:
	LOCAL_UID="$$(id -u)" LOCAL_GID="$$(id -g)" $(SOURCE_COMPOSE) run --rm --build data-source features --config /config/source.json --data-root /data

label-images:
	LOCAL_UID="$$(id -u)" LOCAL_GID="$$(id -g)" ADC_FILE="$${ADC_FILE:-$$HOME/.config/gcloud/application_default_credentials.json}" $(SOURCE_COMPOSE) -f docker-compose.sources.gcp.yml run --rm --build data-source labeling --config /config/source.json --data-root /data --max-images "$(SOURCE_MAX_IMAGES)"

label-report:
	LOCAL_UID="$$(id -u)" LOCAL_GID="$$(id -g)" $(SOURCE_COMPOSE) run --rm --build data-source labeling --config /config/source.json --data-root /data --max-images 0

# New collection to app. Labeling calls require an explicit workflow flag.
WORKFLOW_CONFIG ?= configs/workflow-boston-small.json
WORKFLOW_ARGS ?=
APP_ARGS ?=
RUN_ID ?=
GCS_ROOT ?= gs://ac215-greenpath
PUBLISH_ARGS ?=
.PHONY: run-all catalog-refresh app-versions publish-run
run-all:
	python3 tools/run_all.py --config "$(WORKFLOW_CONFIG)" $(WORKFLOW_ARGS)

catalog-refresh:
	python3 tools/manage_artifacts.py refresh

app-versions:
	python3 tools/manage_artifacts.py serve $(APP_ARGS)

publish-run:
	@test -n "$(RUN_ID)" || (echo "Set RUN_ID to a reviewed successful training run."; exit 1)
	python3 tools/manage_artifacts.py publish-run --run-id "$(RUN_ID)" --gcs-root "$(GCS_ROOT)" $(PUBLISH_ARGS)

# Standard-library coordination checks; no Docker, collection or paid requests.
.PHONY: source-test
source-test:
	PYTHONPATH=src/data-source python3 -m unittest discover -s src/data-source/tests -v
