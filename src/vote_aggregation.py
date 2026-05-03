"""Vote aggregation.

Roll up parish-level vote counts into district-level totals for both
the upper tier (party-list seats) and the lower tier (single-member
winners).

Design choice: when a parish in the vote data has no matching CAOP
geometry (the 'orphan parish' problem documented in design_notes.md),
we use the *municipality* (DTMN, 4-digit prefix of DICOFRE) as the
fallback join key. Since DTMN codes are stable across redistricting
events, every orphan parish's votes will still land in the correct
upper-tier district. Vote totals at the upper-tier level are therefore
preserved exactly; lower-tier totals lose ~10 percent of orphan-parish
votes, but the lower tier operates on the CAOP boundary and orphan
parishes do not exist as polygons there.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def aggregate_votes_to_upper(
    votes_long: pd.DataFrame,
    parish_to_upper: pd.DataFrame,
    municipality_fallback: bool = True,
) -> pd.DataFrame:
    """Aggregate parish-level votes into upper-tier district totals.

    Parameters
    ----------
    votes_long : DataFrame
        Long-format with columns: parish_id, municipality_id, party, votes.
    parish_to_upper : DataFrame
        Mapping table with columns: parish_id, municipality_id, upper_district.
        Produced by upper_tier_redesign step.
    municipality_fallback : bool
        If True, parish IDs that don't match parish_to_upper are joined
        on municipality_id instead. This recovers orphan parishes whose
        DICOFRE codes have changed since the election.

    Returns
    -------
    DataFrame with columns: upper_district, party, votes.
    """
    # First-pass: direct parish join.
    merged = votes_long.merge(
        parish_to_upper[["parish_id", "upper_district"]],
        on="parish_id",
        how="left",
    )
    direct = merged[merged["upper_district"].notna()].copy()
    orphans = merged[merged["upper_district"].isna()].copy()

    if len(orphans) and municipality_fallback:
        # Build municipality -> upper_district map (each municipality
        # belongs to exactly one upper-tier district by construction).
        muni_map = (
            parish_to_upper[["municipality_id", "upper_district"]]
            .drop_duplicates("municipality_id")
        )
        recovered = (
            orphans.drop(columns=["upper_district"])
            .merge(muni_map, on="municipality_id", how="left")
        )
        n_recovered = recovered["upper_district"].notna().sum()
        n_failed = len(recovered) - n_recovered
        logger.info(
            "Vote aggregation: %d orphan parish rows recovered via municipality fallback, "
            "%d still unmappable.", n_recovered, n_failed,
        )
        if n_failed:
            still_orphan = recovered[recovered["upper_district"].isna()]
            logger.warning(
                "Unmappable rows (parish_id not in CAOP and municipality_id not in CAOP): "
                "%d rows, %d votes lost. Examples: %s",
                n_failed, int(still_orphan["votes"].sum()),
                still_orphan["parish_id"].head(5).tolist(),
            )
        direct = pd.concat([direct, recovered[recovered["upper_district"].notna()]], ignore_index=True)

    elif len(orphans):
        logger.warning(
            "Vote aggregation: %d orphan parish rows, %d votes dropped "
            "(municipality_fallback=False).",
            len(orphans), int(orphans["votes"].sum()),
        )

    # Sum votes per (district, party).
    out = (
        direct.groupby(["upper_district", "party"])["votes"]
        .sum()
        .reset_index()
        .sort_values(["upper_district", "votes"], ascending=[True, False])
        .reset_index(drop=True)
    )
    return out


def aggregate_votes_to_lower(
    votes_long: pd.DataFrame,
    parish_to_lower: pd.DataFrame,
    parish_to_municipality: pd.DataFrame | None = None,
    municipality_fallback: bool = True,
) -> pd.DataFrame:
    """Aggregate parish-level votes into lower-tier (single-member) district totals.

    Lower-tier districts are constructed from CAOP parishes, but the
    election data contains some parish IDs that no longer exist in CAOP
    (orphan parishes from freguesia mergers). To preserve as much vote
    data as possible we apply a municipality fallback: an orphan parish
    is mapped to a lower-tier district by sharing the most common lower
    district within its municipality. This is approximate; for a parish
    that crosses a sub-district boundary the votes will land on the
    majority side. The fallback is opt-in via ``municipality_fallback``.

    Parameters
    ----------
    votes_long : DataFrame
        Long-format with columns: parish_id, municipality_id, party, votes.
    parish_to_lower : DataFrame
        Mapping with columns: parish_id, lower_district, parent_upper_district.
    parish_to_municipality : DataFrame, optional
        Required if municipality_fallback=True. Provides parish_id -> municipality_id.
    municipality_fallback : bool
        Whether to recover orphan-parish votes via municipality-mode mapping.

    Returns
    -------
    DataFrame with columns: lower_district, parent_upper_district, party, votes.
    """
    direct = votes_long.merge(
        parish_to_lower[["parish_id", "lower_district", "parent_upper_district"]],
        on="parish_id", how="left",
    )
    matched = direct[direct["lower_district"].notna()].copy()
    orphans = direct[direct["lower_district"].isna()].copy()

    if len(orphans) and municipality_fallback and parish_to_municipality is not None:
        # For each municipality, find its most common lower_district and
        # parent_upper_district, then assign orphan parish votes to that.
        muni_to_lower = (
            parish_to_lower.merge(
                parish_to_municipality[["parish_id", "municipality_id"]],
                on="parish_id",
            )
            .groupby("municipality_id")
            .agg(
                lower_district=("lower_district", lambda x: x.mode().iloc[0]),
                parent_upper_district=("parent_upper_district", lambda x: x.mode().iloc[0]),
            )
            .reset_index()
        )
        recovered = (
            orphans.drop(columns=["lower_district", "parent_upper_district"])
            .merge(muni_to_lower, on="municipality_id", how="left")
        )
        n_recovered = recovered["lower_district"].notna().sum()
        n_failed = len(recovered) - n_recovered
        recovered_votes = int(recovered[recovered["lower_district"].notna()]["votes"].sum())
        logger.info(
            "Lower-tier aggregation: %d orphan parish rows recovered "
            "via municipality-mode fallback (%d votes preserved); %d still unmappable.",
            n_recovered, recovered_votes, n_failed,
        )
        matched = pd.concat(
            [matched, recovered[recovered["lower_district"].notna()]],
            ignore_index=True,
        )
        if n_failed:
            still_orphan = recovered[recovered["lower_district"].isna()]
            logger.warning(
                "Lower-tier aggregation: %d rows (%d votes) could not be mapped "
                "to any lower-tier district.",
                n_failed, int(still_orphan["votes"].sum()),
            )
    elif len(orphans):
        logger.warning(
            "Lower-tier aggregation dropped %d parish-vote rows (%d votes) "
            "(municipality_fallback disabled or unavailable).",
            len(orphans), int(orphans["votes"].sum()),
        )

    out = (
        matched.groupby(["lower_district", "parent_upper_district", "party"])["votes"]
        .sum()
        .reset_index()
        .sort_values(
            ["parent_upper_district", "lower_district", "votes"],
            ascending=[True, True, False],
        )
        .reset_index(drop=True)
    )
    return out


def lower_tier_winners(
    district_votes: pd.DataFrame,
    all_districts: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Determine the winning party in each lower-tier district.

    Simplifying assumption (Methodology section 8):
    Without candidate-level data, we treat the party with the most
    votes in each lower-tier district as the winner. This is a
    counterfactual approximation; under an actual single-member system,
    voter behaviour and candidate selection would differ.

    Parameters
    ----------
    district_votes : DataFrame
        Long-format votes per (lower_district, parent_upper_district, party).
    all_districts : DataFrame, optional
        Diagnostic table covering ALL lower districts (including any with
        zero recorded votes). If supplied, districts with zero votes are
        emitted with winning_party=None and a note.

    Returns
    -------
    DataFrame with one row per lower_district and columns:
        lower_district, parent_upper_district, winning_party, winning_votes,
        runner_up_party, runner_up_votes, margin, margin_pct, assumption_note
    """
    rows = []
    seen = set()
    for (lower, parent), grp in district_votes.groupby(["lower_district", "parent_upper_district"]):
        seen.add(lower)
        sorted_grp = grp.sort_values("votes", ascending=False).reset_index(drop=True)
        winner = sorted_grp.iloc[0]
        runner = sorted_grp.iloc[1] if len(sorted_grp) > 1 else None
        winning_votes = int(winner["votes"])
        runner_votes = int(runner["votes"]) if runner is not None else 0
        margin = winning_votes - runner_votes
        total_votes = int(grp["votes"].sum())
        margin_pct = (margin / total_votes) if total_votes > 0 else 0.0
        rows.append({
            "lower_district": lower,
            "parent_upper_district": parent,
            "winning_party": winner["party"] if winning_votes > 0 else None,
            "winning_votes": winning_votes,
            "runner_up_party": runner["party"] if runner is not None else None,
            "runner_up_votes": runner_votes,
            "margin": margin,
            "margin_pct": round(margin_pct, 4),
            "assumption_note": "party-vote approximation (no candidate-level data)",
        })

    # Emit a row for any district present in the diagnostics but missing from votes.
    if all_districts is not None:
        for _, drow in all_districts.iterrows():
            if drow["lower_district"] not in seen:
                rows.append({
                    "lower_district": drow["lower_district"],
                    "parent_upper_district": drow["parent_upper_district"],
                    "winning_party": None,
                    "winning_votes": 0,
                    "runner_up_party": None,
                    "runner_up_votes": 0,
                    "margin": 0,
                    "margin_pct": 0.0,
                    "assumption_note": "no vote data; parish_ids not present in election dataset",
                })

    return pd.DataFrame(rows).sort_values(
        ["parent_upper_district", "lower_district"],
    ).reset_index(drop=True)
