# The order window ("front desk"): only takes the parameters → hands them to pipeline / agent → returns; no flow or math logic here.
from fastapi import APIRouter, HTTPException

from api import agent, pipeline
from api.schemas import AgentResult, PreferenceRequest, ScoreRequest, ScoreResponse

router = APIRouter()


# Only runs the harness: user's words → weights + record of every Q&A (for debugging and display)
# Use def, not async def: the Gemini call blocks while waiting for the result; FastAPI puts def into a thread pool, so it doesn't block other requests
@router.post("/preference", response_model=AgentResult)
def post_preference(request: PreferenceRequest) -> AgentResult:
    return agent.get_preference(request.text)


# Full flow ①–⑩: hand the whole flow to pipeline.run_score(); turn an unknown region into HTTP 404
@router.post("/score", response_model=ScoreResponse)
def post_score(request: ScoreRequest) -> ScoreResponse:
    try:
        return pipeline.run_score(request.text, request.region_id)
    except pipeline.UnknownRegionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=503, detail=f"Model artifact unavailable or invalid: {e}")
