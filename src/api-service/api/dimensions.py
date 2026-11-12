"""Read the online dimension contract used by Gemini, validation, and scoring."""
import json
from pathlib import Path

_SPEC = json.loads(Path(__file__).with_name("dimensions.json").read_text(encoding="utf-8"))
SCHEMA_VERSION = _SPEC["schema_version"]
SCORE_MIN, SCORE_MAX = _SPEC["score_range"]
DIMENSIONS = {name: spec["description"] for name, spec in _SPEC["dimensions"].items()}
DIMENSION_NAMES = list(DIMENSIONS)
