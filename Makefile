GCS_SERVING_URI ?= gs://ac215-greenpath/serving/boston-heldout-v1

.PHONY: app fetch-serving verify-serving app-gcs test
app:
	docker compose up --build

fetch-serving:
	cd api-service && uv run --frozen --python 3.12 python -m api.artifacts fetch $(GCS_SERVING_URI) ../.artifacts/boston-heldout-v1

verify-serving:
	cd api-service && uv run --frozen --python 3.12 python -m api.artifacts verify ../.artifacts/boston-heldout-v1

app-gcs: fetch-serving
	MODEL_BUNDLE_DIR=/artifacts/boston-heldout-v1 docker compose up --build

test:
	cd api-service && uv run --frozen --python 3.12 pytest -q
