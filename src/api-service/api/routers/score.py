# 接单窗口（"前台"）：只负责收参数 → 交给 pipeline / agent → 返回，不放流程和计算逻辑。
from fastapi import APIRouter, HTTPException

from api import agent, pipeline, tiles
from api.schemas import AgentResult, PreferenceRequest, ScoreRequest, ScoreResponse

router = APIRouter()


# 只跑 harness：用户的话 → 权重 + 每次问答记录（调试、展示用）
# 用 def 不用 async def：Gemini 调用会卡住等结果，def 会被 FastAPI 放进线程池，不堵其他请求
@router.post("/preference", response_model=AgentResult)
def post_preference(request: PreferenceRequest) -> AgentResult:
    return agent.get_preference(request.text)


# 区域的格子清单（不调 Gemini）：前端打开页面时先画出整片区域
@router.get("/regions/{region_id}")
def get_region(region_id: str) -> dict:
    try:
        return {"region_id": region_id, "h3_index": [t.h3_index for t in tiles.get_tiles(region_id)]}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# 完整流程 ①–⑩：整条流程交给 pipeline.run_score()；不认识的区域转成 HTTP 404
@router.post("/score", response_model=ScoreResponse)
def post_score(request: ScoreRequest) -> ScoreResponse:
    try:
        return pipeline.run_score(request.text, request.region_id)
    except pipeline.UnknownRegionError as e:
        raise HTTPException(status_code=404, detail=str(e))
