# The "reader" for the scenery dimension table: the real definition is dimensions.json in the same folder (one shared rule set for the whole team); this file only reads it.
# Other files still import DIMENSIONS / DIMENSION_NAMES the same way as before.
# To change dimensions, only edit dimensions.json; after that, remember to rerun scripts/fake_scores_from_image.py (the fake model's scores need to change too).
import json
from pathlib import Path

# Read dimensions.json (in the same folder as this file)
_SPEC = json.loads(Path(__file__).with_name("dimensions.json").read_text(encoding="utf-8"))

# Version number of the dimension table: bump it whenever the dimensions change; labels and models should both record which version they use. schema_version comes from the json
SCHEMA_VERSION = _SPEC["schema_version"]

# Score range of the model output (team agreement: 0-1): schemas.py uses it to check the scores the model sends back. score_range comes from the json
SCORE_MIN, SCORE_MAX = _SPEC["score_range"]

# The dimension names and descriptions that actually get sent to Gemini
DIMENSIONS = {name: spec["description"] for name, spec in _SPEC["dimensions"].items()}

# Just the list of names, for other files to use
DIMENSION_NAMES = list(DIMENSIONS)
