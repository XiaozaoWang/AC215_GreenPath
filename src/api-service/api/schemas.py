# 数据契约（pydantic）：凡是跨边界传的数据，都在这里规定长什么样。
#   Gemini → harness ：Preference
#   模型   → api     ：TileScore
#   浏览器 ↔ api     ：ScoreRequest / ScoreResponse
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from api.dimensions import DIMENSION_NAMES, SCORE_MAX, SCORE_MIN

# 合法维度名的白名单：只认 dimensions.py 里有的名字
DimensionName = Literal[tuple(DIMENSION_NAMES)]


# ---------- Gemini → harness ----------
# 一个维度的权重：直接要的给满分，联想到的给部分分；reason 写在 weight 前面，让 Gemini 先想理由再给分
class DimensionWeight(BaseModel):
    dimension: DimensionName
    reason: str = Field(description="Very short reason, in the user's language")
    weight: float = Field(ge=-1, le=1, description="1 = strongly wants, -1 = strongly avoids, in between = partial / associated")


# prompt分析结果：只列直接提到的和联想到的维度，没列的算 0
# llm.ask返回json版本，之后通过继承的pydantic库中model_validate_json检查并转化为Preference格式
class Preference(BaseModel): #继承pydantic库的基础类
    weights: list[DimensionWeight] = []
    must_have: list[DimensionName] = [] #用户说必须要有的dimension

    # 检查：同一个维度不能出现两次
    @model_validator(mode="after")
    def no_duplicate_dimensions(self):
        names = [w.dimension for w in self.weights]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"each dimension may appear once, duplicated: {dupes}")
        return self

    # 展开成完整的权重向量 w：每个维度都有值，没提到的补 0
    def as_vector(self) -> dict[str, float]:
        vector = {name: 0.0 for name in DIMENSION_NAMES}
        for w in self.weights:
            vector[w.dimension] = w.weight
        return vector


# 一次问答的记录（Trace）：发了什么、Gemini 回了什么、为什么被打回
class Attempt(BaseModel):
    prompt: str
    raw: str
    error: str | None = None


# agent.py返回的 harness的完整结果：上方的Preference + 每次问答记录 + 是否用了保底
class AgentResult(BaseModel):
    preference: Preference
    attempts: list[Attempt]
    used_fallback: bool


# tiles.py 中输出list[Tile]
# 每个Tile代表一个 H3 六边形格子（res 10，约 130 m 宽）；前端用 h3-js 从编号算出六边形的边界
class Tile(BaseModel):
    h3_index: str


# model_client.py中 将视觉模型输出结果和list[Tile]计算得出的list[TileScore]
# 每一个TileScore代表一个格子的模型打分 = Tile + 这个格子每个维度分（量程看 dimensions.json 的 score_range，全组约定 0-1）
class TileScore(Tile):
    scores: dict[str, float]

    # 检查：维度必须一个不多一个不少，分数必须在约定量程内（0-1）
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


# 打分过程的中间数字（给页面展示）：每维的区域均值和 σ、总分范围、被门槛刷掉几格
class ScoreStats(BaseModel):
    n_tiles: int
    n_excluded: int
    t_min: float | None
    t_max: float | None
    mean: dict[str, float]
    sigma: dict[str, float]


# 浏览器发来的请求：只要偏好
class PreferenceRequest(BaseModel):
    text: str = Field(min_length=1, examples=["周末想沿着河骑，最好有树荫，别有太多车"])


# 浏览器发来的请求：偏好 + 打哪个区域
class ScoreRequest(PreferenceRequest):
    region_id: str = "cambridge"


# 热图里的一个格子：H3 编号 + 总分 t（None = 没过 must_have 门槛）
class CellValue(BaseModel):
    h3_index: str
    value: float | None


# 返回给浏览器的结果：最终热图 + 每一步的中间结果（页面上能展示的越多越好）
class ScoreResponse(BaseModel):
    region_id: str
    agent: AgentResult  # ②–⑤ 偏好 + 每次问答记录
    weights: dict[str, float]  # 展开后的完整权重 w（每个维度都有，没提到的是 0）
    cells: list[CellValue]  # ⑩ 每个六边形格子的总分 t
    stats: ScoreStats  # 打分过程的中间数字
    timings_ms: dict[str, float]  # 每一步花了多少毫秒
