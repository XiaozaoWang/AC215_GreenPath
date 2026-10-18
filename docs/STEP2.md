# Step 2: reloadable M1 model and independent image prediction

## Result and scope

Step 1 connected saved predictions to the application. Step 2 makes the selected M1
baseline runnable outside notebooks: fit, export, reload and predict. New components
follow the professor's `src/<component>/` layout and use separate uv environments.

```text
Existing NAIP image -> feature-encoder (PyTorch / frozen CLIP)
                            |
                       512 features
                            |
                model-deploy (NumPy / saved scaler and Ridge heads)
                            |
                  eight named predictions

Frozen dataset + cached embeddings + prior selected configuration
                            |
                model-training (scikit-learn, no PyTorch)
                            |
                  versioned regression artifact
```

The existing API/frontend stay in their current directories and still use the step-1
geographic saved-prediction fixture. These commands do not yet change `/score` to run
image inference. Data collection, teacher labeling, full-region application integration
and Docker orchestration of the complete ML workflow remain separate tasks.

## Components

| Directory | Responsibility | Environment |
| --- | --- | --- |
| `src/feature-encoder` | Package local CLIP weights; encode images with notebook-equivalent preprocessing | Python 3.11, PyTorch / OpenCLIP |
| `src/model-training` | Refit the already-selected M1, evaluate and export; prepare minimal training inputs | Python 3.11, scikit-learn / SciPy |
| `src/model-deploy` | Verify model artifacts, reload numeric state and predict batches | Python 3.11, NumPy / pandas; no PyTorch or scikit-learn |

Each has `pyproject.toml`, `uv.lock`, source, tests and README. Local uv path dependencies
reuse the inference/artifact package rather than copying its implementation. Preserve
the sibling directory layout when installing. Future container builds must use a context
that includes these sibling packages; Dockerfiles are not added in this step.

## What was reproduced

- Historical experiment: `baselines-first-run-20261013T0242`.
- Selected encoder: CLIP ViT-B/16, `laion2b_s34b_b88k`, 512 raw features.
- Image processing: RGB; integer 224 center crop without resizing; divide by 255;
  CLIP channel normalization; mean over four rotations and four flipped rotations;
  no embedding L2 normalization.
- StandardScaler fitted only on 824 train+validation rows.
- Eight independent Ridge heads, alpha 1000, with per-target missing-label filtering.
- Predictions clipped to [0,1], preserving the eight-target order.
- No new parameter search, encoder training, data collection or paid teacher calls.

M1 is a trained regression model on frozen image features, not an end-to-end fine-tuned
CLIP model. Its held-out mean Spearman is approximately 0.44245, reproducing the old
baseline rather than claiming an improvement.

## Versioned GCS artifacts

Project: `organic-victory-508017-v9`; bucket: `gs://ac215-greenpath`.

| Prefix | Contents |
| --- | --- |
| `models/m1-clip-ridge-v1` | Scaler and Ridge numeric state, model contract, split IDs, metrics and reload validation |
| `encoders/clip-b16-laion2b-v1` | Local safetensors checkpoint plus exact preprocessing and weight/source identity |
| `training-inputs/m1-refit-inputs-v1` | Frozen v1 dataset, schema, selected CLIP cache, experiment config and reference predictions |
| `samples/boston-image-smoke-v1` | Three existing NAIP images, input records and historical sample embeddings |

All four were published and downloaded into new directories with file checksum
verification. Existing step-1 GCS prefixes were not overwritten. The encoder weights
are approximately 571 MiB; model/data artifacts are much smaller. Weights and numeric
training data stay out of Git; small metadata and verification evidence are committed.

The training-input snapshot is sufficient for this M1 refit, not a full raw training
dataset or all-backbone research archive. Its schema notes are translated to English;
numeric tables are byte-for-byte source copies and original hashes remain recorded.

## Install component environments

From the repository root, with uv installed:

```sh
uv sync --frozen --python 3.11 --project src/model-deploy
uv sync --frozen --python 3.11 --project src/model-training
uv sync --frozen --python 3.11 --project src/feature-encoder
```

The API artifact CLI uses its existing Python 3.12 environment. GCS access requires
each teammate's authorized ADC and bucket permissions; credentials are not included.

## Download prepared artifacts

From the repository root:

```sh
cd api-service
uv sync --frozen --python 3.12
uv run --frozen python -m api.artifacts fetch \
  gs://ac215-greenpath/models/m1-clip-ridge-v1 \
  ../.artifacts/models/m1-clip-ridge-v1
uv run --frozen python -m api.artifacts fetch \
  gs://ac215-greenpath/encoders/clip-b16-laion2b-v1 \
  ../.artifacts/encoders/clip-b16-laion2b-v1
uv run --frozen python -m api.artifacts fetch \
  gs://ac215-greenpath/samples/boston-image-smoke-v1 \
  ../.artifacts/samples/boston-image-smoke-v1
uv run --frozen python -m api.artifacts fetch \
  gs://ac215-greenpath/training-inputs/m1-refit-inputs-v1 \
  ../.artifacts/training-inputs/m1-refit-inputs-v1
cd ..
```

## Run image-to-prediction in one command

After the model, encoder and sample are available locally:

```sh
make predict-images SMOKE_VERSION=my-image-smoke-001
```

This first starts the encoder process, then a separate model prediction process. Output:

```text
.artifacts/embeddings/my-image-smoke-001-embeddings-v1/
    embeddings.parquet, embedding.json, manifest.json
.artifacts/predictions/my-image-smoke-001-predictions-v1/
    scores.json, prediction.json, manifest.json
```

Output directories are immutable. Choose a new `SMOKE_VERSION` for another run; the
command refuses to overwrite an existing result. For your own image input, override
`IMAGE_INPUT` with an absolute JSON path and `IMAGE_ROOT` with the image data root:

```json
[{"cell_id": "my-cell", "tile_path": "relative/path/to/image.png"}]
```

Images must be at least 224 pixels in both dimensions. This stage does not perform
geographic crop/download operations; provide tiles compatible with the training data.
The CLI returns records keyed by the supplied IDs and does not invent geographic bounds.

## Refit using only downloaded training inputs

From the repository root:

```sh
make train-m1 \
  DATA_ROOT="$PWD/.artifacts/training-inputs/m1-refit-inputs-v1" \
  MODEL_VERSION=my-m1-refit-001
```

The selected config is already inside the snapshot at
`results/baselines-first-run-20261013T0242/config.json`. The command fits only train+val,
compares test predictions with the historical result, and reloads the artifact in a fresh
process. Any mismatch beyond the declared tolerances aborts publication of the output.
Use `TRAIN_CONFIG` for another explicitly supported config; this initial implementation
only supports the selected CLIP eight-view M1 path, not every model in notebook 07.

For a local research data root, supply that absolute directory as `DATA_ROOT` instead.
No original notebook or research input is modified.

## Prepare new input/encoder versions

The training snapshot can be recreated without rerunning data notebooks. From the repository root:

```sh
cd src/model-training
uv run --frozen greenpath-prepare-training --data-root /absolute/research/data \
  --output /absolute/new-snapshot --artifact-version NEW-INPUT-VERSION
cd ../..
```

To package an existing locally downloaded checkpoint, from the repository root (never put weights in Git):

```sh
cd src/feature-encoder
uv run --frozen greenpath-encode export --checkpoint /absolute/model.safetensors \
  --source-revision SOURCE-COMMIT --output /absolute/new-encoder \
  --artifact-version NEW-ENCODER-VERSION
cd ../..
```

The current checkpoint source revision is
`7288da5a0d6f0b51c4a2b27c624837a9236d0112`, SHA-256
`3f25d29d3cc74e1d25d47e0593b4dd0864ced1e9b4d8e486a247ca4502f227f1`.
The original embedding metadata did not record this digest. Three-image re-encoding
establishes numerical compatibility for those images, not exact provenance for all
1,012 historical cache rows. New encoding outputs record checkpoint and image hashes.

## Artifact design and validation

`weights.npz` stores scaler mean/scale and each Ridge head's coefficients/intercept.
It is loaded with `allow_pickle=False`. Inference matches the notebook's float32 scaler
rounding and per-head dot products. A file manifest checks completeness and SHA-256;
dimensions, shapes, finite values, positive scales, input columns and IDs are validated.
Numeric arrays avoid executable Python model serialization and remove scikit-learn
from the deployment environment. This format currently supports this scaler/Ridge
combination; a different estimator needs its own adapter and format version.

Versioned encoder outputs must identify the same encoder manifest as the regression
model. Historical notebook caches are accepted but explicitly marked as historical
inputs whose encoder identity is not independently guaranteed by their columns alone.

Test-set RMSE is a reproduction/reporting metric. It is not automatically injected into
the application as an error floor or used for new model selection.

```sh
make ml-test
```

Validation evidence is in `docs/step2/evidence/`:

- Refit vs historical test predictions: maximum absolute difference **0.0**.
- Reloaded vs fitted prediction, including a fresh process: maximum difference
  **1.1920928955078125e-7**, tolerance **1e-6**.
- Three CPU-encoded images vs old MPS/batch cache: embedding difference
  **3.933906555175781e-6**; declared comparison uses atol/rtol **2e-3**.
- Image-based vs cached-feature predictions: maximum difference
  **3.5762786865234375e-7**.
- Independent regression environment has neither PyTorch nor scikit-learn installed.
- Makefile image prediction and refit from GCS-downloaded inputs were exercised.
- All 25 new tests passed (20 model/training tests and 5 encoder preprocessing tests).
- Unit tests, raw logs, sample input/output, model/encoder metadata and cloud validation
  status are retained. These checks are separate from the earlier API/browser evidence.

## Remaining work

1. Integrate model-derived predictions and geographic coverage with the API/frontend.
2. Extract remaining data tasks from notebooks and containerize the components.
3. Compose a complete data-to-training-to-app workflow with a documented one-command run.
4. Resolve historical input provenance differences and publish complete required data.

`make predict-images` is a real image inference command; it is not yet the complete
Milestone 2 ingestion/preprocessing/training/application pipeline.
