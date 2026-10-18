# Upstream notebook 01-04 commands

This package contains static Python extractions of the English notebooks. It does not
execute notebooks or require Jupyter/Colab at runtime. Original notebooks are unchanged.

| Notebook | Module / CLI stage | Container service |
| --- | --- | --- |
| 01 streetview | `greenpath_source.streetview` / `streetview` | `source-streetview` |
| 02 tiles | `greenpath_source.tiles` / `tiles` | `source-tiles` |
| 03 OSM + WorldCover | `greenpath_source.features` / `features` | `source-features` |
| 04 teacher labels | `greenpath_source.labeling` / `labeling` | `source-labeling` |

The Dockerfile accepts `SOURCE_STAGE` and installs that stage's requirements. Each
service gets its own image; dependencies are not added to the existing four ML images.
Direct dependency versions were transcribed from the existing research environment.
Transitive dependencies are resolved by pip at build time, not fully locked. No image
build or runtime verification was performed for this extraction, at the user's request.

Use `greenpath-source STAGE --config FILE --data-root DIRECTORY`. Labeling additionally
accepts `--max-images N`: 0 uses only the durable label cache, a positive integer limits
new images selected (retries may issue multiple API calls per image). No automatic
smoke-test or sandbox calls run before the cache check.

Commands, configuration, outputs, credentials, partial labeling and the existing
training-flow handoff are documented in [SOURCE_PIPELINE.md](../../docs/SOURCE_PIPELINE.md).
Interactive notebook displays are exported as PNG/HTML/Parquet reports. Human annotation
remains a manual task. See `notebook-provenance.json` for the extraction references.
