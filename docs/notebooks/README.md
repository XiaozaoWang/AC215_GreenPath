# Research notebook archive

This directory preserves all seven original research notebooks and their seven English
counterparts. They were moved from the repository root without changing file bytes,
cell contents, outputs or metadata. The `en_` prefix identifies the English counterpart.

## Index

| Step | Original notebook | English counterpart | Purpose | Current container code |
| --- | --- | --- | --- | --- |
| 01 | [01_streetview.ipynb](01_streetview.ipynb) | [en_01_streetview.ipynb](en_01_streetview.ipynb) | Street-view collection | [src/data-source/](../../src/data-source/) |
| 02 | [02_tiles.ipynb](02_tiles.ipynb) | [en_02_tiles.ipynb](en_02_tiles.ipynb) | Aerial tile collection | [src/data-source/](../../src/data-source/) |
| 03 | [03_osm_features.ipynb](03_osm_features.ipynb) | [en_03_osm_features.ipynb](en_03_osm_features.ipynb) | OSM / WorldCover features | [src/data-source/](../../src/data-source/) |
| 04 | [04_labeling.ipynb](04_labeling.ipynb) | [en_04_labeling.ipynb](en_04_labeling.ipynb) | Teacher labeling | [src/data-source/](../../src/data-source/) |
| 05 | [05_build_dataset.ipynb](05_build_dataset.ipynb) | [en_05_build_dataset.ipynb](en_05_build_dataset.ipynb) | Dataset construction and spatial splits | [src/data-processor/](../../src/data-processor/) |
| 06 | [06_embeddings.ipynb](06_embeddings.ipynb) | [en_06_embeddings.ipynb](en_06_embeddings.ipynb) | Image feature extraction | [src/feature-encoder/](../../src/feature-encoder/) |
| 07 | [07_baselines.ipynb](07_baselines.ipynb) | [en_07_baselines.ipynb](en_07_baselines.ipynb) | Baseline experiments and regression training | [src/model-training/](../../src/model-training/) |

The independent reload / batch prediction / serving stage is implemented in
[src/model-deploy/](../../src/model-deploy/); there is no separate notebook 08.
The productionized path selects the CLIP + Ridge workflow rather than reproducing
every experimental backbone, baseline and visualization in these notebooks.

## Execution and paths

Use the root [Makefile](../../Makefile) and [workflow guide](../ML_PIPELINE.md) for the
containerized pipeline. Moving the notebooks does not change Compose configuration,
data locations, model locations or the existing Python CLI entry points.

These are historical research documents, not automatically updated deployment settings.
If you choose to execute one manually, first inspect its storage, credential and prompt
configuration. Relative paths such as `./data`, `.env` and `prompts/` depend on the
kernel working directory, which may now be this notebook directory. Set the intended
kernel working directory to the project root or use explicit absolute paths in the
notebook configuration before execution. Do not accidentally create a new research
dataset under this documentation directory or reuse the historical example bucket
without reviewing it. Existing notebook configurations were deliberately preserved.

## Extraction provenance

Upstream extraction references retain their original notebook basenames and cell
numbers. [notebook-provenance.json](../../src/data-source/greenpath_source/notebook-provenance.json)
adds each archived notebook's repository path while retaining its recorded SHA-256.
Moving files does not change a content hash. Historical run manifests remain untouched.
