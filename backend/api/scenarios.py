"""Scenario discovery and config endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import (
    Party,
    PartyListResponse,
    ScenarioConfig,
    ScenarioListItem,
    ScenarioListResponse,
)
from ..services import (
    OutputNotFoundError,
    get_config_service,
    get_output_service,
)

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


@router.get("", response_model=ScenarioListResponse, summary="List available scenarios")
def list_scenarios():
    out = get_output_service()
    items: list[ScenarioListItem] = []
    for sid in out.list_scenarios():
        try:
            summary = out.read_json(sid, "json/scenario_summary.json")
        except OutputNotFoundError:
            continue
        items.append(ScenarioListItem(
            scenario_id=summary.get("scenario_id", sid),
            scenario_name=summary.get("scenario_name", sid),
            election_year=summary.get("election_year", 0),
        ))
    return ScenarioListResponse(data=items)


@router.get(
    "/{scenario_id}/config",
    response_model=ScenarioConfig,
    summary="Get one scenario's config and totals",
)
def get_scenario_config(scenario_id: str):
    out = get_output_service()
    try:
        summary = out.read_json(scenario_id, "json/scenario_summary.json")
    except OutputNotFoundError:
        raise HTTPException(status_code=404, detail=f"Scenario not found: {scenario_id}")
    return ScenarioConfig(**summary)


@router.get(
    "/{scenario_id}/parties",
    response_model=PartyListResponse,
    summary="Get the party metadata table",
)
def get_parties(scenario_id: str):
    out = get_output_service()
    if scenario_id not in out.list_scenarios():
        raise HTTPException(status_code=404, detail=f"Scenario not found: {scenario_id}")

    cfg_svc = get_config_service()
    df = cfg_svc.parties()
    items = [
        Party(
            party_id=row["party_id_short"],
            party_id_raw=row["party_id"],
            party_name=row["display_name"],
            abbreviation=row["short_name"],
            color=row["color"],
            notes=(row["notes"] if isinstance(row["notes"], str) else None) or None,
        )
        for _, row in df.iterrows()
    ]
    return PartyListResponse(scenario_id=scenario_id, data=items)
