"""The model contract is the source of truth. Restart after changing it."""
import json
import math
import os
from pathlib import Path


def load_spec(path: Path) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(spec.get("schema_version"), str) or not spec["schema_version"]:
        raise ValueError("schema_version is required")
    bounds = spec.get("score_range", [])
    if len(bounds) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bounds) or bounds[0] >= bounds[1]:
        raise ValueError("score_range must contain two finite increasing numbers")
    dimensions = spec.get("dimensions")
    if not isinstance(dimensions, dict) or not dimensions:
        raise ValueError("dimensions must be a nonempty mapping")
    for name, detail in dimensions.items():
        if not isinstance(detail, dict) or not detail.get("description") or not name:
            raise ValueError(f"invalid dimension: {name}")
    return spec


PROVIDER = os.environ.get("MODEL_PROVIDER", "bundle")
if PROVIDER not in {"bundle", "legacy"}:
    raise ValueError("MODEL_PROVIDER must be bundle or legacy")
BUNDLE_DIR = Path(os.environ.get("MODEL_BUNDLE_DIR", str(Path(__file__).parent.parent / "fixtures" / "boston-heldout-en-v2")))
default_schema = Path(__file__).with_name("dimensions.legacy.json" if PROVIDER == "legacy" else "dimensions.json")
if PROVIDER == "bundle" and (BUNDLE_DIR / "schema.json").exists():
    default_schema = BUNDLE_DIR / "schema.json"
SPEC_PATH = Path(os.environ.get("MODEL_SCHEMA_PATH", str(default_schema)))
SPEC = load_spec(SPEC_PATH)
SCHEMA_VERSION = SPEC["schema_version"]
SCORE_MIN, SCORE_MAX = SPEC["score_range"]
DIMENSIONS = {name: detail["description"] for name, detail in SPEC["dimensions"].items()}
DIMENSION_NAMES = list(DIMENSIONS)
