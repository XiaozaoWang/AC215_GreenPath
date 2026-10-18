"""Boundary models validated against the active model's versioned contract."""
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from api.dimensions import DIMENSION_NAMES, SCORE_MIN, SCORE_MAX

DimensionName = Literal[tuple(DIMENSION_NAMES)]


class DimensionWeight(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    dimension: DimensionName
    reason: str
    weight: float = Field(ge=-1, le=1)


class Preference(BaseModel):
    weights: list[DimensionWeight] = Field(default_factory=list)
    must_have: list[DimensionName] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list, description="Requested concepts this model cannot score; never invent substitute dimensions")

    @model_validator(mode='after')
    def no_duplicate_dimensions(self):
        names = [w.dimension for w in self.weights]
        if len(names) != len(set(names)) or len(self.must_have) != len(set(self.must_have)):
            raise ValueError('duplicate dimensions are not allowed')
        positive = {w.dimension for w in self.weights if w.weight > 0}
        if not set(self.must_have).issubset(positive):
            raise ValueError('must_have requires an explicit positive preference for the dimension')
        return self

    def as_vector(self) -> dict[str, float]:
        return {name: next((w.weight for w in self.weights if w.dimension == name), 0.0) for name in DIMENSION_NAMES}


class Attempt(BaseModel):
    prompt: str
    raw: str
    error: str | None = None


class AgentResult(BaseModel):
    preference: Preference
    attempts: list[Attempt]
    used_fallback: bool


class Tile(BaseModel):
    tile_id: str = Field(min_length=1)
    row: int | None = Field(default=None, ge=0)
    col: int | None = Field(default=None, ge=0)
    h3_index: str | None = None
    geometry: dict | None = None

    @model_validator(mode='after')
    def position(self):
        if (self.row is None) != (self.col is None):
            raise ValueError('row and col must be provided together')
        if self.geometry is None and self.row is None:
            raise ValueError('a geographic geometry or a grid position is required')
        return self


class TileScore(Tile):
    scores: dict[str, float]

    @field_validator('scores')
    @classmethod
    def check_scores(cls, scores):
        if set(scores) != set(DIMENSION_NAMES):
            raise ValueError(f'dimension mismatch: expected {DIMENSION_NAMES}, got {list(scores)}')
        if any(not math.isfinite(v) or not SCORE_MIN <= v <= SCORE_MAX for v in scores.values()):
            raise ValueError(f'scores must be finite and in [{SCORE_MIN}, {SCORE_MAX}]')
        return scores


class ScoreStats(BaseModel):
    n_tiles: int
    n_excluded: int
    t_min: float | None
    t_max: float | None
    mean: dict[str, float]
    sigma: dict[str, float]


class PreferenceRequest(BaseModel):
    text: str = Field(min_length=1, examples=['A quiet ride with shade, avoid traffic'])


class ScoreRequest(PreferenceRequest):
    region_id: str | None = None
    version_id: str | None = None


class ScoreResponse(BaseModel):
    preview: bool = False
    version_id: str | None = None
    dataset_version: str | None = None
    source_version: str | None = None
    snapshot_version: str | None = None
    run_id: str | None = None
    region_id: str
    agent: AgentResult
    weights: dict[str, float]
    stats: ScoreStats
    timings_ms: dict[str, float]
    schema_version: str
    model_version: str
    prediction_source: str
    description: str
    rmse_source: str
    features: list[dict] = Field(default_factory=list)
    rows: int = 0
    cols: int = 0
    heatmap: list[list[float | None]] | None = None
