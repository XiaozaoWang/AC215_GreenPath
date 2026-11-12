"""Backend route contract. Add the real road-graph calculation in plan_route when available."""
from typing import Literal

from pydantic import BaseModel, Field


class Coordinate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RouteRequest(BaseModel):
    origin: Coordinate
    destination: Coordinate
    region_id: str = "city_ma_boston_big"
    text: str = Field(default="", max_length=2000)


class RouteGeometry(BaseModel):
    type: Literal["LineString"] = "LineString"
    coordinates: list[tuple[float, float]] = Field(min_length=2)


class RouteResponse(BaseModel):
    region_id: str
    geometry: RouteGeometry
    distance_m: float = Field(ge=0)


def plan_route(request: RouteRequest) -> RouteResponse:
    # No road graph or routing implementation is included in this repository yet.
    raise NotImplementedError("Route planning is unavailable: no routing engine has been configured")
