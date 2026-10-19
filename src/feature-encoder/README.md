# Frozen CLIP image encoder

This component extracts notebook 06's selected CLIP ViT-B/16 image path. It runs in
its own Python/uv environment, separate from scikit-learn and LightGBM.

- Source weights: `laion/CLIP-ViT-B-16-laion2B-s34B-b88K`, pinned by source revision
  and full SHA-256 in the exported encoder artifact.
- Preprocessing: RGB, integer center crop to 224 without resizing, uint8/255,
  CLIP channel normalization, four rotations plus four rotated horizontal flips.
- Output: mean of eight raw 512-dimensional embeddings, with no L2 normalization.
- Encoding loads a verified local safetensors file; it does not automatically download
  model weights. `--device cpu` is portable; MPS/CUDA are explicit options.

For local development, run from `src/feature-encoder/`:

```sh
uv sync --frozen --python 3.11
uv run --frozen greenpath-encode --help
uv run --frozen pytest -q
```

Input JSON is a list of `{cell_id, tile_path}` records, with image paths relative to
`--data-root`. Output is a checksummed embedding artifact with image hashes and encoder
identity. It is a vision feature extractor, not a vector database or RAG component.

The encoder's historical cache did not record a weight digest. A sample re-encoding
comparison demonstrates numerical compatibility, not proof about every old cache row.
See [ML_PIPELINE.md](../../docs/ML_PIPELINE.md) for reproducible commands and artifact locations.
Build the Linux CPU image from the repository root; see [ML_PIPELINE.md](../../docs/ML_PIPELINE.md).
New embedding artifacts record actual runtime versions and per-batch progress as well
as the separately versioned checkpoint identity.

## Pipeline inputs and outputs

Compose service `feature-encoder` in `docker-compose.ml.yml` runs after `data-processor`:

- Input list: `.artifacts/pipeline/runs/<run-id>/prepared/images.json`.
- Images: `.artifacts/pipeline/cache/images/<snapshot-version>/`.
- Encoder: `.artifacts/pipeline/cache/encoders/<encoder-version>/`, containing
  `encoder.json`, `model.safetensors` and `manifest.json`.
- Output: `.artifacts/pipeline/runs/<run-id>/embeddings/`, containing
  `embeddings.parquet`, `embedding.json` and `manifest.json`.

The pipeline ingests encoder weights before this job; the container has no network
access and runs on CPU. It extracts features without training CLIP. From the repository
root, `make run` includes this job automatically. Uploaded run embeddings retain the same
relative path under `gs://ac215-greenpath/artifacts/pipeline/runs/<run-id>/embeddings/`;
GCS publication is explicit, not part of encoding.
