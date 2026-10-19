# Milestone 2 app design

[Current application wireframe](app/m2-wireframe.svg) documents the implemented layout.
The earlier diagrams under `app/` describe the original app-side architecture and are
retained as historical context. This new wireframe is not presented as the original M1
submission or as a runtime screenshot.

Implementation-driven design changes:

- Replace the simulated 20-dimension contract with the model's eight actual outputs.
- Render real H3 polygons instead of assigning arbitrary rows to a rectangular map.
- Load labels, examples, region and capability information from the API.
- Show model/provenance information and explicitly identify unscored preferences.
- Adapt the inspector and map layout to smaller windows.
- Let the pipeline feed a newly generated serving bundle without changing UI code.

The team should attach or identify its actual M1 mock-up and any TF feedback when
preparing the presentation. No feedback history is invented here. Route generation,
new image uploads and per-request CLIP inference remain outside this minimal app.
