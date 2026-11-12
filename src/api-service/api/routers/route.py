from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from api import model_client, road_graph, routing
from api.routing import RoutePlan, RoutePlanRequest, RouteRequest, RouteResponse

router = APIRouter(tags=["routing"])


def _http_error(exc: Exception) -> HTTPException:
    # Route calculation stays in the backend; the frontend only forwards requests.
    if isinstance(exc, road_graph.UnknownGraphError):
        return HTTPException(404, str(exc))
    if isinstance(exc, road_graph.NoRouteError):
        return HTTPException(422, str(exc))
    if isinstance(exc, model_client.ModelTimeoutError):
        return HTTPException(504, str(exc))
    return HTTPException(502, str(exc))


_EXPECTED = (road_graph.UnknownGraphError, road_graph.NoRouteError, model_client.ModelServiceError)


@router.post("/route/plan", response_model=RoutePlan)
def plan(request: RoutePlanRequest):
    """The shortest route and the cells that will be predicted. Instant; needs no text."""
    try:
        return routing.plan(request)
    except _EXPECTED as exc:
        raise _http_error(exc) from exc


@router.post("/route/stream")
def stream(request: RouteRequest):
    """Plan routes and report progress as newline-delimited JSON events (see routing.stream)."""
    try:
        graph, prep = routing.prepare(request)      # unknown region and unreachable points are plain HTTP errors
    except _EXPECTED as exc:
        raise _http_error(exc) from exc
    return StreamingResponse(routing.stream(request, graph, prep), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/route", response_model=RouteResponse)
def plan_route(request: RouteRequest):
    try:
        return routing.plan_route(request)
    except _EXPECTED as exc:
        raise _http_error(exc) from exc
