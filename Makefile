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
