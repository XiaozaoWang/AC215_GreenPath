from fastapi import APIRouter, HTTPException

from api import routing
from api.routing import RouteRequest, RouteResponse

router = APIRouter(tags=["routing"])


@router.post("/route", response_model=RouteResponse)
def plan_route(request: RouteRequest):
    # Keep route calculation in the backend; the frontend only forwards requests.
    try:
        return routing.plan_route(request)
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc)) from exc
