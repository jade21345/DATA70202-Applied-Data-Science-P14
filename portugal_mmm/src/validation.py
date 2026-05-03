"""Validation utilities.

Each function checks one specific invariant and raises a ValueError with
a clear message if it fails. The intent is to fail loudly and early in
the pipeline rather than to silently produce bad outputs that the
frontend then displays.

All checks are pure functions that take DataFrames / GeoDataFrames as
input and return None on success.
"""
from __future__ import annotations

import logging
from typing import Iterable

import geopandas as gpd
import networkx as nx
import pandas as pd

logger = logging.getLogger(__name__)


def require_columns(df: pd.DataFrame, required: Iterable[str], name: str = "DataFrame") -> None:
    """Raise if any required column is missing."""
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{name} is missing required columns: {missing}")


def require_unique(df: pd.DataFrame, key: str, name: str = "DataFrame") -> None:
    """Raise if values in ``key`` column are not unique."""
    if df[key].duplicated().any():
        dups = df.loc[df[key].duplicated(), key].head(5).tolist()
        raise ValueError(f"{name}.{key} contains duplicates, e.g. {dups}")


def require_one_to_one_assignment(
    parishes: pd.DataFrame,
    parish_col: str,
    assignment_col: str,
    name: str = "assignment",
) -> None:
    """Each parish must be assigned to exactly one district.

    Checks: (a) no NaN in ``assignment_col``, (b) parish_col is unique.
    """
    require_unique(parishes, parish_col, name)
    if parishes[assignment_col].isna().any():
        n = int(parishes[assignment_col].isna().sum())
        raise ValueError(
            f"{name}: {n} parishes have NaN in '{assignment_col}'. "
            "Every parish must be assigned to a district."
        )


def require_hamilton_total(hamilton_df: pd.DataFrame, total_seats: int) -> None:
    """Sum of Hamilton-allocated mandates must equal the configured total."""
    s = int(hamilton_df["total_mandates"].sum())
    if s != total_seats:
        raise ValueError(
            f"Hamilton allocation sums to {s}, expected {total_seats}."
        )


def require_tier_split_consistency(tier_df: pd.DataFrame) -> None:
    """For every district: party_list_seats + single_member_seats == total_mandates,
    and both components are non-negative."""
    bad = tier_df[
        tier_df["party_list_seats"] + tier_df["single_member_seats"]
        != tier_df["total_mandates"]
    ]
    if len(bad):
        raise ValueError(
            f"tier_split inconsistency in {len(bad)} rows. Sample:\n"
            f"{bad.head(3).to_string()}"
        )
    if (tier_df["party_list_seats"] < 0).any() or (tier_df["single_member_seats"] < 0).any():
        raise ValueError("tier_split contains negative seats.")


def require_dhondt_seat_counts_match(
    dhondt_results: pd.DataFrame,
    tier_split: pd.DataFrame,
    district_col: str = "upper_district",
) -> None:
    """For every upper-tier district, the sum of seats allocated by D'Hondt
    across all parties must equal the planned party_list_seats."""
    actual = dhondt_results.groupby(district_col)["allocated_seats"].sum()
    planned = tier_split.set_index(district_col)["party_list_seats"]
    diff = (actual - planned).fillna(actual)
    bad = diff[diff != 0]
    if len(bad):
        raise ValueError(
            f"D'Hondt seat totals do not match tier_split.party_list_seats "
            f"for {len(bad)} districts:\n{bad.to_string()}"
        )


def require_lower_tier_nested(
    lower_membership: pd.DataFrame,
    upper_membership: pd.DataFrame,
    parish_col: str = "parish_id",
    upper_col: str = "upper_district",
    parent_col: str = "parent_upper_district",
) -> None:
    """Every lower-tier district must be entirely contained in one upper-tier district.

    Equivalent to: for every parish, the upper district recorded in
    lower_membership.parent_upper_district must equal the actual upper
    district in upper_membership.
    """
    upper_lookup = upper_membership.set_index(parish_col)[upper_col].to_dict()
    bad = []
    for _, row in lower_membership.iterrows():
        if upper_lookup.get(row[parish_col]) != row[parent_col]:
            bad.append(row[parish_col])
    if bad:
        raise ValueError(
            f"{len(bad)} parishes have inconsistent upper/lower-tier nesting. "
            f"Examples: {bad[:5]}"
        )


def check_contiguity(
    parishes: gpd.GeoDataFrame,
    adjacency: nx.Graph,
    group_col: str,
    parish_col: str = "parish_id",
) -> dict[str, bool]:
    """Return {group_value: is_contiguous} for each group in ``group_col``.

    A group is contiguous if the subgraph of the adjacency graph induced
    by its parish set is a single connected component. This is the
    standard test for redistricting algorithms.

    Note: islands without virtual bridges will always show as
    non-contiguous; whether that is acceptable depends on the use case
    (typically yes for upper-tier groups containing Madeira/Açores,
    debatable for lower-tier).
    """
    parish_to_node = {p: i for i, p in enumerate(parishes[parish_col])}
    out: dict[str, bool] = {}
    for grp_value, sub in parishes.groupby(group_col):
        nodes = [parish_to_node[p] for p in sub[parish_col]]
        if len(nodes) == 0:
            out[grp_value] = True
            continue
        sg = adjacency.subgraph(nodes)
        out[grp_value] = nx.is_connected(sg)
    return out


def vote_preservation_check(
    raw_long: pd.DataFrame,
    aggregated: pd.DataFrame,
    party_col: str = "party",
    votes_col: str = "votes",
    tolerance: int = 0,
) -> None:
    """Total votes per party must match between raw and aggregated tables.

    ``tolerance`` allows for a small rounding budget if integer truncation
    is unavoidable; default 0 means exact match.
    """
    raw_totals = raw_long.groupby(party_col)[votes_col].sum().sort_index()
    agg_totals = aggregated.groupby(party_col)[votes_col].sum().sort_index()
    diff = (raw_totals - agg_totals).fillna(raw_totals)
    bad = diff[diff.abs() > tolerance]
    if len(bad):
        raise ValueError(
            f"Vote totals not preserved after aggregation for {len(bad)} parties:\n"
            f"{bad.to_string()}"
        )
