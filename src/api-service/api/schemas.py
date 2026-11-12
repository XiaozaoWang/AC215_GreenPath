"""Shared contracts for preferences, model predictions, and browser responses."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from api.dimensions import DIMENSION_NAMES, SCORE_MAX, SCORE_MIN

DimensionName = Literal[tuple(DIMENSION_NAMES)]
PredictionStatus = Literal["ok", "not_downloaded", "no_naip_coverage", "mostly_black"]


class DimensionWeight(BaseModel):
    dimension: DimensionName
    reason: str = Field(description="Very short reason, in the user's language")
    weight: float = Field(ge=-1, le=1, description="1 = strongly wants, -1 = strongly avoids, in between = partial / associated")


class Preference(BaseModel):
    weights: list[DimensionWeight] = []
    must_have: list[DimensionName] = []

    @model_validator(mode="after")
    def no_duplicate_dimensions(self):
        names = [w.dimension for w in self.weights]
        if len(names) != len(set(names)):
            raise ValueError("each dimension may appear only once")
        return self

    def as_vector(self) -> dict[str, float]:
        # Dimensions Gemini did not mention have zero weight.
        vector = dict.fromkeys(DIMENSION_NAMES, 0.0)
        vector.update({w.dimension: w.weight for w in self.weights})
        return vector


class Attempt(BaseModel):
    prompt: str
    raw: str
    error: str | None = None


class AgentResult(BaseModel):
    preference: Preference
    attempts: list[Attempt]
    used_fallback: bool


class Tile(BaseModel):
    h3_index: str


class TileScore(Tile):
    status: PredictionStatus = "ok"
    scores: dict[str, float] | None

    @model_validator(mode="after")
    def check_prediction(self):
        # Unavailable cells must have null scores; usable cells need exactly 15 finite scores in [0, 1].
        if self.status != "ok":
            if self.scores is not None:
                raise ValueError("Unavailable cells require scores=null")
        elif self.scores is None or set(self.scores) != set(DIMENSION_NAMES):
            raise ValueError("status=ok requires scores for exactly the 15 model dimensions")
        elif any(not SCORE_MIN <= value <= SCORE_MAX for value in self.scores.values()):
            raise ValueError(f"scores must be in [{SCORE_MIN}, {SCORE_MAX}]")
        return self


class ScoreStats(BaseModel):
    n_tiles: int
    n_excluded: int
    t_min: float | None
    t_max: float | None
    mean: dict[str, float]
    sigma: dict[str, float]


class PreferenceRequest(BaseModel):
    text: str = Field(min_length=1, examples=["A shady ride by the water"])


class ScoreRequest(PreferenceRequest):
    region_id: str = "city_ma_boston_big"


class CellValue(Tile):
    value: float | None  # Null means unavailable or excluded by a must-have threshold.
    status: PredictionStatus = "ok"


class ScoreResponse(BaseModel):
    region_id: str
    agent: AgentResult
    weights: dict[str, float]
    cells: list[CellValue]
    stats: ScoreStats
    timings_ms: dict[str, float]
