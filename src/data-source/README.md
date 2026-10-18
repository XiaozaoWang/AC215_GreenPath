# Upstream notebook 01-04 commands

This package contains static Python extractions of the English notebooks. It does not
execute notebooks or require Jupyter/Colab at runtime. Original notebooks are unchanged.

| Notebook | Module / CLI stage | Container service | uv extra |
| --- | --- | --- | --- |
| 01 streetview | `greenpath_source.streetview` / `streetview` | `source-streetview` | `streetview` |
| 02 tiles | `greenpath_source.tiles` / `tiles` | `source-tiles` | `tiles` |
| 03 OSM + WorldCover | `greenpath_source.features` / `features` | `source-features` | `features` |
| 04 teacher labels | `greenpath_source.labeling` / `labeling` | `source-labeling` | `labeling` |

Like the downstream ML components and API, this folder contains `Dockerfile`,
`pyproject.toml`, `uv.lock` and `.python-version`. Direct and transitive dependencies are
recorded in `uv.lock`; requirements files are no longer a second dependency source.

The four stages share one code package and lockfile. The Dockerfile selects its stage
with `SOURCE_STAGE` and installs `uv sync --frozen --no-dev --extra STAGE` using uv 0.12.12
into `/opt/venv`. Each image installs common dependencies plus only its selected extra.
For example, the streetview image does not install Gemini or pyrosm. The `features` extra
explicitly includes the geospatial dependencies it shares with `tiles`.

Local dependency setup, when needed, uses the same lock and extra:

```sh
uv sync --frozen --no-dev --extra streetview
```

Use `greenpath-source STAGE --config FILE --data-root DIRECTORY`. Labeling additionally
accepts `--max-images N`: 0 uses only the durable label cache, a positive integer limits
new images selected (retries may issue multiple API calls per image). No automatic
smoke-test or sandbox calls run before the cache check.

The user verified 01-04 with 50 cells, 192 street images, 50 aerial tiles, 50 feature rows
and 9 labeled images before this dependency migration. Those runs do not verify the new
uv-built images; rebuilding and runtime verification are pending user execution.

Commands, configuration, outputs, credentials, partial labeling and the existing
training-flow handoff are documented in [SOURCE_PIPELINE.md](../../docs/SOURCE_PIPELINE.md).
Interactive notebook displays are exported as PNG/HTML/Parquet reports. Human annotation
remains a manual task. See `notebook-provenance.json` for the extraction references.
For the shared dependency convention, see [DEPENDENCIES.md](../../docs/DEPENDENCIES.md).
