# Four ML component containers

Each ML component has a Dockerfile in its own `src/` folder. Build from the repository
root because data, encoding and training depend on the sibling model package:

```sh
docker build -f src/data-processor/Dockerfile -t greenpath-data-processor:ml .
docker build -f src/feature-encoder/Dockerfile -t greenpath-feature-encoder:ml .
docker build -f src/model-training/Dockerfile -t greenpath-model-training:ml .
docker build -f src/model-deploy/Dockerfile -t greenpath-model-deploy:ml .
```

| Image | Default command | Runtime role |
| --- | --- | --- |
| `greenpath-data-processor:ml` | `greenpath-data` | Ingest frozen source versions; join/filter/split data |
| `greenpath-feature-encoder:ml` | `greenpath-encode` | Frozen CLIP encoding and actual image preprocessing |
| `greenpath-model-training:ml` | `greenpath-train` | Fit scaler/Ridge and export reloadable state |
| `greenpath-model-deploy:ml` | `greenpath-predict` | Reload model and predict batches |

All use Python 3.11, uv 0.12.12 and `uv sync --frozen --no-dev`. The encoding lock
selects the official CPU-only PyTorch index on Linux, avoiding unused CUDA libraries.
Mac local installs retain their platform-specific packages. Weights are mounted from
versioned caches, never copied into an image. Images default to non-root.

`docker run --rm IMAGE --help` displays options. Supply read-only inputs and writable
output mounts. On host bind mounts, use `--user UID:GID` matching the output owner.
The pipeline launcher sets these automatically.

Individual checks are recorded in `pipeline/evidence/individual-container-checks.json`
and per-component logs. They disable networking for all four local computations.
Authenticated GCS ingestion is checked separately in the composed pipeline.

## Historical reproduction versus new pipeline training

The default training command retains step 2's strict historical prediction comparison.
For a newly prepared dataset, provide `--embeddings PATH` pointing to a checksummed
encoder artifact and `--reference-policy none`. This still verifies split separation,
encoder identity and saved-versus-reloaded predictions. New data is not claimed to
reproduce an old experiment. Metrics report the new run's held-out split.
