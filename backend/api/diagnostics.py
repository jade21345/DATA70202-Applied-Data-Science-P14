"""Diagnostics endpoint."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas import DiagnosticsResponse
from ..services import get_output_service, run_diagnostics

router = APIRouter(prefix="/api/scenarios", tags=["diagnostics"])


@router.get(
    "/{scenario_id}/diagnostics",
    response_model=DiagnosticsResponse,
    summary="Validation status, warnings, and integrity checks",
)
def get_diagnostics(scenario_id: str):
    out = get_output_service()
    if scenario_id not in out.list_scenarios():
        raise HTTPException(status_code=404, detail=f"Scenario not found: {scenario_id}")
    return DiagnosticsResponse(**run_diagnostics(out, scenario_id))
