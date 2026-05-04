"""Map (GeoJSON) endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import OutputNotFoundError, get_output_service

router = APIRouter(prefix="/api/scenarios", tags=["maps"])


@router.get(
    "/{scenario_id}/maps/upper-districts",
    summary="Upper-tier district polygons (GeoJSON, EPSG:4326)",
)
def get_upper_districts_map(scenario_id: str):
    out = get_output_service()
    if scenario_id not in out.list_scenarios():
        raise HTTPException(status_code=404, detail=f"Scenario not found: {scenario_id}")
    try:
        return out.read_geojson(scenario_id, "geojson/upper_districts.geojson")
    except OutputNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/{scenario_id}/maps/lower-districts",
    summary="Lower-tier (single-member) district polygons (GeoJSON, EPSG:4326)",
)
def get_lower_districts_map(scenario_id: str):
    out = get_output_service()
    if scenario_id not in out.list_scenarios():
        raise HTTPException(status_code=404, detail=f"Scenario not found: {scenario_id}")
    try:
        return out.read_geojson(scenario_id, "geojson/lower_districts.geojson")
    except OutputNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
