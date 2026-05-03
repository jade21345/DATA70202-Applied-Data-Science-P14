"""Final result combination.

Take the per-district D'Hondt allocation (party-list seats) and the
per-district lower-tier winners (single-member seats) and combine them
into national party totals plus seat shares.

Two output products:
- party_seat_breakdown : long, with district granularity preserved
- final_party_seat_results : wide, one row per party with national totals
"""
from __future__ import annotations

import pandas as pd


def combine_results(
    dhondt_by_district: pd.DataFrame,
    single_member_winners: pd.DataFrame,
    parties_meta: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Combine party-list seats and single-member seats into national totals.

    Parameters
    ----------
    dhondt_by_district : DataFrame
        Long-format with columns: upper_district, party, votes, allocated_seats
    single_member_winners : DataFrame
        One row per lower-tier district, columns include winning_party.
    parties_meta : DataFrame, optional
        Lookup table with party_id, display_name, short_name, color.
        If supplied, the final result table will include display fields.

    Returns
    -------
    breakdown : DataFrame
        Long format, one row per (party, upper_district), with columns:
            upper_district, party, party_list_seats, single_member_seats,
            total_seats
    final : DataFrame
        Wide format, one row per party, sorted by total_seats descending,
        with columns:
            party, party_list_seats, single_member_seats, total_seats,
            seat_share, [display_name, short_name, color if parties_meta given]
    """
    # Party-list breakdown by district.
    pl_by_dist = (
        dhondt_by_district.groupby(["upper_district", "party"])["allocated_seats"]
        .sum()
        .reset_index()
        .rename(columns={"allocated_seats": "party_list_seats"})
    )

    # Single-member breakdown by parent_upper_district.
    sm_by_dist = (
        single_member_winners.groupby(["parent_upper_district", "winning_party"])
        .size()
        .reset_index(name="single_member_seats")
        .rename(columns={
            "parent_upper_district": "upper_district",
            "winning_party": "party",
        })
    )

    # Outer-join district x party.
    breakdown = pl_by_dist.merge(
        sm_by_dist, on=["upper_district", "party"], how="outer"
    ).fillna(0)
    breakdown["party_list_seats"] = breakdown["party_list_seats"].astype(int)
    breakdown["single_member_seats"] = breakdown["single_member_seats"].astype(int)
    breakdown["total_seats"] = breakdown["party_list_seats"] + breakdown["single_member_seats"]
    breakdown = breakdown.sort_values(
        ["upper_district", "total_seats"], ascending=[True, False]
    ).reset_index(drop=True)

    # National-level final.
    final = (
        breakdown.groupby("party")[["party_list_seats", "single_member_seats", "total_seats"]]
        .sum()
        .reset_index()
        .sort_values("total_seats", ascending=False)
        .reset_index(drop=True)
    )
    grand = int(final["total_seats"].sum())
    final["seat_share"] = (final["total_seats"] / grand).round(4) if grand else 0.0

    if parties_meta is not None:
        final = final.merge(
            parties_meta[["party_id", "display_name", "short_name", "color"]],
            left_on="party", right_on="party_id", how="left",
        ).drop(columns=["party_id"])

    return breakdown, final


def parliament_to_json(final: pd.DataFrame, total_seats: int) -> dict:
    """Render the final parliament composition as a JSON-serialisable dict.

    Output schema (used by the static frontend):
    {
        "total_seats": 226,
        "parties": [
            {"party": "PS", "display_name": "...", "short_name": "PS",
             "color": "#FF66B2", "party_list_seats": 50,
             "single_member_seats": 20, "total_seats": 70, "seat_share": 0.31},
            ...
        ]
    }
    """
    parties = []
    for _, row in final.iterrows():
        parties.append({
            "party": row["party"],
            "display_name": row.get("display_name") if "display_name" in final.columns else None,
            "short_name": row.get("short_name") if "short_name" in final.columns else None,
            "color": row.get("color") if "color" in final.columns else None,
            "party_list_seats": int(row["party_list_seats"]),
            "single_member_seats": int(row["single_member_seats"]),
            "total_seats": int(row["total_seats"]),
            "seat_share": float(row["seat_share"]),
        })
    return {
        "total_seats": int(total_seats),
        "allocated_seats": int(final["total_seats"].sum()),
        "parties": parties,
    }
