"""Pydantic response schemas.

Each model defines the canonical wire format for one type of API
response. The backend imports these and uses them as response_model on
the FastAPI routes, which gives us automatic OpenAPI docs and runtime
validation: any drift in the underlying CSV/GeoJSON outputs will fail
loudly on the next request rather than silently sending bad data to
the frontend.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str = Field(..., description="ok if backend is alive")
    n_scenarios: int = Field(..., ge=0)


class ScenarioListItem(BaseModel):
    scenario_id: str
    scenario_name: str
    election_year: int


class ScenarioListResponse(BaseModel):
    data: list[ScenarioListItem]


class ScenarioConfig(BaseModel):
    scenario_id: str
    scenario_name: str
    election_year: int
    total_seats: int
    party_list_ratio: float
    single_member_ratio: float
    tier_split_rounding: str
    district_apportionment_method: str
    party_list_allocation_method: str
    lower_tier_method: str
    n_upper_districts: int
    n_lower_districts: int
    party_list_seats_total: int
    single_member_seats_total: int
    allocated_seats: int
    data_version: str
    created_at: str

    model_config = ConfigDict(extra="allow")


class Party(BaseModel):
    party_id: str = Field(..., description="Slug-style id (e.g. 'psd_cds')")
    party_id_raw: str = Field(..., description="Raw id as in source data")
    party_name: str
    abbreviation: str
    color: str = Field(..., pattern=r"^#[0-9A-Fa-f]{6}$")
    notes: Optional[str] = None


class PartyListResponse(BaseModel):
    scenario_id: str
    data: list[Party]


class FinalResultRow(BaseModel):
    party_id: str
    party_name: str
    abbreviation: str
    party_colour: str
    party_list_seats: int
    single_member_seats: int
    total_seats: int
    seat_share: float


class FinalResultResponse(BaseModel):
    scenario_id: str
    election_year: int
    total_seats: int
    allocated_seats: int
    data: list[FinalResultRow]


class HamiltonRow(BaseModel):
    upper_district_id: str
    upper_district_name: str
    registered_voters: int
    voter_share: float
    quota: float
    floor_seats: int
    remainder: float
    extra_seat: int
    total_mandates: int


class HamiltonResponse(BaseModel):
    scenario_id: str
    total_seats: int
    data: list[HamiltonRow]


class TierSplitRow(BaseModel):
    upper_district_id: str
    upper_district_name: str
    total_mandates: int
    party_list_seats: int
    single_member_seats: int


class TierSplitResponse(BaseModel):
    scenario_id: str
    rounding_rule: str
    data: list[TierSplitRow]


class DhondtRow(BaseModel):
    upper_district_id: str
    upper_district_name: str
    party_id: str
    party_name: str
    abbreviation: str
    color: str
    votes: int
    allocated_seats: int


class DhondtResponse(BaseModel):
    scenario_id: str
    data: list[DhondtRow]


class SingleMemberWinnerRow(BaseModel):
    lower_district_id: str
    lower_district_name: str
    parent_upper_district_id: str
    parent_upper_district_name: str
    winner_party_id: Optional[str] = None
    winner_party_name: Optional[str] = None
    winner_party_color: Optional[str] = None
    winning_votes: int
    runner_up_party_id: Optional[str] = None
    runner_up_votes: int
    margin: int
    margin_pct: float
    assumption_note: str


class SingleMemberWinnersResponse(BaseModel):
    scenario_id: str
    data: list[SingleMemberWinnerRow]


class DiagnosticCheck(BaseModel):
    check_name: str
    passed: bool
    severity: str = Field(..., description="info | warning | error")
    message: str


class DiagnosticsResponse(BaseModel):
    scenario_id: str
    status: str = Field(..., description="valid | valid_with_warnings | invalid")
    checks: list[DiagnosticCheck]


class ErrorResponse(BaseModel):
    detail: str


# GeoJSON is a deeply-nested structure that Pydantic can describe but
# at the cost of a lot of code that adds little value (the structure is
# fixed by RFC 7946 and well-known). We just accept any dict-of-dicts.
GeoJSONResponse = dict[str, Any]
