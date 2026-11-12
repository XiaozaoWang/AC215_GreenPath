"""HTTP endpoints translate input and service failures; pipeline.py owns scoring."""
from fastapi import APIRouter, HTTPException

from api import agent, model_client, pipeline, tiles
from api.schemas import AgentResult, PreferenceRequest, ScoreRequest, ScoreResponse

router = APIRouter()


@router.post("/preference", response_model=AgentResult)
def post_preference(request: PreferenceRequest) -> AgentResult:
    return agent.get_preference(request.text)


@router.get("/regions/{region_id}")
def get_region(region_id: str) -> dict:
    try:
        return {"region_id": region_id, "h3_index": [t.h3_index for t in tiles.get_tiles(region_id)]}
    except tiles.UnknownRegionError as exc:
        raise HTTPException(404, str(exc)) from exc
    except tiles.RegionLoadError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/score", response_model=ScoreResponse)
def post_score(request: ScoreRequest) -> ScoreResponse:
    # These synchronous handlers run in FastAPI's thread pool during cloud I/O.
    try:
        return pipeline.run_score(request.text, request.region_id)
    except tiles.UnknownRegionError as exc:
        raise HTTPException(404, str(exc)) from exc
    except tiles.RegionLoadError as exc:
        raise HTTPException(503, str(exc)) from exc
    except model_client.ModelTimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except model_client.ModelServiceError as exc:
        raise HTTPException(502, str(exc)) from exc
