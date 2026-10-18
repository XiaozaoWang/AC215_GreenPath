# GreenPath application — active model contract

The current application reads a model-owned schema and a geographic prediction bundle.
Start with `docs/STEP1.md`. The old 20-dimension, 100×100 simulated demo is preserved
in `LEGACY_DEMO.md` and can be run explicitly with `MODEL_PROVIDER=legacy`.

The default is **188 real Boston H3 cells and saved M1 held-out predictions**.
This is an interface fixture, not a deployed regression model or live image inference.
