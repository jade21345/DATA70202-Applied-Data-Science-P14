"""Tabular result endpoints (final seats, Hamilton, tier split, D'Hondt, SM winners).

Each endpoint reads its corresponding CSV from outputs/scenarios/<id>/tables/,
joins party metadata where helpful, and returns a typed JSON response.
"""
from __future__ import annotations

import math

import pandas as pd
from fastapi import APIRouter, HTTPException

from ..schemas import (
    DhondtResponse,
    DhondtRow,
    FinalResultResponse,
    FinalResultRow,
    HamiltonResponse,
    HamiltonRow,
    SingleMemberWinnerRow,
    SingleMemberWinnersResponse,
    TierSplitResponse,
    TierSplitRow,
)
from ..services import (
    OutputNotFoundError,
    get_config_service,
    get_output_service,
)

# Reuse the slugify helper from the algorithm side for consistency.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
from slugs import slugify  # noqa: E402

router = APIRouter(prefix="/api/scenarios", tags=["results"])


def _ensure_scenario(scenario_id: str) -> None:
    out = get_output_service()
    if scenario_id not in out.list_scenarios():
        raise HTTPException(status_code=404, detail=f"Scenario not found: {scenario_id}")


def _safe_float(x) -> float:
    """Convert a numeric-like value to a finite float (0.0 for NaN/inf)."""
    try:
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except (TypeError, ValueError):
        return 0.0


@router.get(
    "/{scenario_id}/results/final",
    response_model=FinalResultResponse,
    summary="Final national party seat totals",
)
def get_final_results(scenario_id: str):
    _ensure_scenario(scenario_id)
    out = get_output_service()
    cfg_svc = get_config_service()

    summary = out.read_json(scenario_id, "json/scenario_summary.json")
    df = out.read_csv(scenario_id, "tables/final_party_seat_results.csv")
    party_lookup = cfg_svc.party_lookup_by_raw()

    rows: list[FinalResultRow] = []
    for _, r in df.iterrows():
        meta = party_lookup.get(r["party"], {})
        rows.append(FinalResultRow(
            party_id=meta.get("party_id_short", slugify(r["party"])),
            party_name=meta.get("display_name", str(r["party"])),
            abbreviation=meta.get("short_name", str(r["party"])),
            party_colour=meta.get("color", "#888888"),
            party_list_seats=int(r["party_list_seats"]),
            single_member_seats=int(r["single_member_seats"]),
            total_seats=int(r["total_seats"]),
            seat_share=_safe_float(r["seat_share"]),
        ))

    return FinalResultResponse(
        scenario_id=scenario_id,
        election_year=int(summary["election_year"]),
        total_seats=int(summary["total_seats"]),
        allocated_seats=int(summary["allocated_seats"]),
        data=rows,
    )


@router.get(
    "/{scenario_id}/results/hamilton",
    response_model=HamiltonResponse,
    summary="Hamilton apportionment table",
)
def get_hamilton(scenario_id: str):
    _ensure_scenario(scenario_id)
    out = get_output_service()
    summary = out.read_json(scenario_id, "json/scenario_summary.json")
    df = out.read_csv(scenario_id, "tables/hamilton_allocation.csv")
    rows = [
        HamiltonRow(
            upper_district_id=slugify(r["upper_district"]),
            upper_district_name=str(r["upper_district"]),
            registered_voters=int(r["registered_voters"]),
            voter_share=_safe_float(r["voter_share"]),
            quota=_safe_float(r["quota"]),
            floor_seats=int(r["floor_seats"]),
            remainder=_safe_float(r["remainder"]),
            extra_seat=int(r["extra_seat"]),
            total_mandates=int(r["total_mandates"]),
        )
        for _, r in df.iterrows()
    ]
    return HamiltonResponse(
        scenario_id=scenario_id,
        total_seats=int(summary["total_seats"]),
        data=rows,
    )


@router.get(
    "/{scenario_id}/results/tier-split",
    response_model=TierSplitResponse,
    summary="Per-district party-list vs single-member seat split",
)
def get_tier_split(scenario_id: str):
    _ensure_scenario(scenario_id)
    out = get_output_service()
    summary = out.read_json(scenario_id, "json/scenario_summary.json")
    df = out.read_csv(scenario_id, "tables/tier_split.csv")
    rows = [
        TierSplitRow(
            upper_district_id=slugify(r["upper_district"]),
            upper_district_name=str(r["upper_district"]),
            total_mandates=int(r["total_mandates"]),
            party_list_seats=int(r["party_list_seats"]),
            single_member_seats=int(r["single_member_seats"]),
        )
        for _, r in df.iterrows()
    ]
    return TierSplitResponse(
        scenario_id=scenario_id,
        rounding_rule=str(summary.get("tier_split_rounding", "floor")),
        data=rows,
    )


@router.get(
    "/{scenario_id}/results/dhondt",
    response_model=DhondtResponse,
    summary="D'Hondt party-list seat allocation per district and party",
)
def get_dhondt(scenario_id: str):
    _ensure_scenario(scenario_id)
    out = get_output_service()
    cfg_svc = get_config_service()
    df = out.read_csv(scenario_id, "tables/dhondt_results_by_district.csv")
    party_lookup = cfg_svc.party_lookup_by_raw()
    rows = []
    for _, r in df.iterrows():
        meta = party_lookup.get(r["party"], {})
        rows.append(DhondtRow(
            upper_district_id=slugify(r["upper_district"]),
            upper_district_name=str(r["upper_district"]),
            party_id=meta.get("party_id_short", slugify(r["party"])),
            party_name=meta.get("display_name", str(r["party"])),
            abbreviation=meta.get("short_name", str(r["party"])),
            color=meta.get("color", "#888888"),
            votes=int(r["votes"]),
            allocated_seats=int(r["allocated_seats"]),
        ))
    return DhondtResponse(scenario_id=scenario_id, data=rows)


@router.get(
    "/{scenario_id}/results/single-member-winners",
    response_model=SingleMemberWinnersResponse,
    summary="Winning party for each lower-tier (single-member) district",
)
def get_single_member_winners(scenario_id: str):
    _ensure_scenario(scenario_id)
    out = get_output_service()
    cfg_svc = get_config_service()
    df = out.read_csv(scenario_id, "tables/single_member_winners.csv")
    lookup = cfg_svc.party_lookup_by_raw()

    def party_meta(raw: str | None) -> tuple[str | None, str | None, str | None]:
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            return None, None, None
        meta = lookup.get(raw, {})
        return (
            meta.get("party_id_short", slugify(raw)),
            meta.get("display_name", raw),
            meta.get("color"),
        )

    rows: list[SingleMemberWinnerRow] = []
    for _, r in df.iterrows():
        win_id, win_name, win_color = party_meta(r.get("winning_party"))
        run_id, _, _ = party_meta(r.get("runner_up_party"))
        rows.append(SingleMemberWinnerRow(
            lower_district_id=slugify(r["lower_district"]),
            lower_district_name=str(r["lower_district"]),
            parent_upper_district_id=slugify(r["parent_upper_district"]),
            parent_upper_district_name=str(r["parent_upper_district"]),
            winner_party_id=win_id,
            winner_party_name=win_name,
            winner_party_color=win_color,
            winning_votes=int(r["winning_votes"]),
            runner_up_party_id=run_id,
            runner_up_votes=int(r["runner_up_votes"]),
            margin=int(r["margin"]),
            margin_pct=_safe_float(r["margin_pct"]),
            assumption_note=str(r["assumption_note"]),
        ))
    return SingleMemberWinnersResponse(scenario_id=scenario_id, data=rows)
