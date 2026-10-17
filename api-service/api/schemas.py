# Data contracts (pydantic): any data passed across a boundary has its shape defined here.
#   Gemini  → harness : Preference
#   model   → api     : TileScore
#   browser ↔ api     : ScoreRequest / ScoreResponse
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from api.dimensions import DIMENSION_NAMES, SCORE_MAX, SCORE_MIN

# Whitelist of valid dimension names: only accept names that exist in dimensions.py
DimensionName = Literal[tuple(DIMENSION_NAMES)]


# ---------- Gemini → harness ----------
# Weight for one dimension: things asked for directly get full points, associated ones get partial points; reason comes before weight so Gemini thinks of the reason first, then gives the score
class DimensionWeight(BaseModel):
    dimension: DimensionName
    reason: str = Field(description="Very short reason, in the user's language")
    weight: float = Field(ge=-1, le=1, description="1 = strongly wants, -1 = strongly avoids, in between = partial / associated")


# Result of analyzing the prompt: only list the dimensions mentioned directly or associated; the ones not listed count as 0
# llm.ask returns the JSON version; then model_validate_json from the inherited pydantic library checks it and turns it into the Preference format
class Preference(BaseModel): #inherits the base class from the pydantic library
    weights: list[DimensionWeight] = []
    must_have: list[DimensionName] = [] #dimensions the user says must be there

    # Check: the same dimension can't appear twice
    @model_validator(mode="after")
    def no_duplicate_dimensions(self):
        names = [w.dimension for w in self.weights]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"each dimension may appear once, duplicated: {dupes}")
        return self

    # Expand into the full weight vector w: all 20 dimensions have a value, the ones not mentioned get 0
    def as_vector(self) -> dict[str, float]:
        vector = {name: 0.0 for name in DIMENSION_NAMES}
        for w in self.weights:
            vector[w.dimension] = w.weight
        return vector


# Record of one Q&A (Trace): what was sent, what Gemini replied, and why it was rejected
class Attempt(BaseModel):
    prompt: str
    raw: str
    error: str | None = None


# The full harness result returned by agent.py: the Preference above + the record of every Q&A + whether the fallback was used
class AgentResult(BaseModel):
    preference: Preference
    attempts: list[Attempt]
    used_fallback: bool


# Output list[Tile] in tiles.py
# Each Tile stands for one tile: id + position in the grid
class Tile(BaseModel):
    tile_id: str
    row: int
    col: int


# In model_client.py, the list[TileScore] computed from the vision model output and list[Tile]
# Each TileScore is the model's score for one tile = Tile + this tile's score for each dimension (for the range see score_range in dimensions.json; team agreement 0-1)
class TileScore(Tile):
    scores: dict[str, float]

    # Check: the dimensions must be exactly right, not one more or less, and the scores must be within the agreed range (0-1)
    @field_validator("scores")
    @classmethod
    def check_scores(cls, scores):
        if set(scores) != set(DIMENSION_NAMES):
            missing = set(DIMENSION_NAMES) - set(scores)
            extra = set(scores) - set(DIMENSION_NAMES)
            raise ValueError(f"dimension mismatch, missing={sorted(missing)} extra={sorted(extra)}")
        bad = {k: v for k, v in scores.items() if not SCORE_MIN <= v <= SCORE_MAX}
        if bad:
            raise ValueError(f"scores must be in [{SCORE_MIN}, {SCORE_MAX}]: {bad}")
        return scores


# Intermediate numbers of the scoring process (shown on the page): region mean and σ for each dimension, total score range, how many tiles were knocked out by the gate
class ScoreStats(BaseModel):
    n_tiles: int
    n_excluded: int
    t_min: float | None
    t_max: float | None
    mean: dict[str, float]
    sigma: dict[str, float]


# Request from the browser: only the preference
class PreferenceRequest(BaseModel):
    text: str = Field(min_length=1, examples=["周末想沿着河骑，最好有树荫，别有太多车"])


# Request from the browser: preference + which region to score
class ScoreRequest(PreferenceRequest):
    region_id: str = "demo"


# Result returned to the browser: final heatmap + the intermediate results of every step (the more the page can show, the better)
class ScoreResponse(BaseModel):
    region_id: str
    agent: AgentResult  # ②–⑤ preference + record of every Q&A
    weights: dict[str, float]  # the full expanded weights w (20 dimensions, 0 for the ones not mentioned)
    rows: int
    cols: int
    heatmap: list[list[float | None]]  # ⑩ total score t per tile, heatmap[row][col]; None = didn't pass the must_have gate
    stats: ScoreStats  # intermediate numbers of the scoring process
    timings_ms: dict[str, float]  # how many milliseconds each step took
