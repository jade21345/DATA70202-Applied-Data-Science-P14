"""Upper-tier redesign: turn the original 18 mainland distritos + the
island districts into the redesigned plurinominal districts used by the
mixed-member model.

Three operations, applied in order:

1. **Permanent merging** (always_merged): Madeira (Ilha da Madeira +
   Ilha de Porto Santo) and Açores (nine islands) each collapse into one
   electoral district. This is structural and not negotiable.

2. **Configured merging** (merge_groups): small mainland distritos that
   the client has decided to merge are combined into named macro-regions
   (Trás-os-Montes, Alentejo, Beira Baixa as of 2025). The decision of
   *which* distritos belong to *which* group is supplied by the client
   in scenario_config.json; this module simply applies the mapping.

3. **Algorithmic splitting** (split_rules): large distritos (Lisboa,
   Porto) are split into k contiguous sub-districts of approximately
   equal registered voters using a balanced-partition algorithm that
   operates on the municipality level. The choice of k is configured;
   the actual sub-district composition is computed each year from the
   current voter data.

Each parish ends up assigned to exactly one redesigned upper-tier district.
"""
from __future__ import annotations

import logging
from typing import Iterable

import geopandas as gpd
import networkx as nx
import pandas as pd

from spatial_utils import balanced_contiguous_partition, build_adjacency_graph

logger = logging.getLogger(__name__)


def redesign_upper_tier(
    parishes: gpd.GeoDataFrame,
    voters_by_parish: pd.DataFrame,
    merge_groups: list,
    always_merged: list,
    split_rules: list,
    parish_col: str = "parish_id",
    distrito_col: str = "distrito_ilha",
    municipio_col: str = "municipio",
    voters_col: str = "registered_voters",
) -> gpd.GeoDataFrame:
    """Apply merging and splitting rules to produce redesigned upper-tier districts.

    Parameters
    ----------
    parishes : GeoDataFrame
        One row per parish, with at least ``parish_col``, ``distrito_col``,
        ``municipio_col``, and a geometry column.
    voters_by_parish : DataFrame
        Must contain ``parish_col`` and ``voters_col``. Used to weight
        the contiguous-balanced-partition step for Lisboa/Porto.
    merge_groups, always_merged : list of MergeGroup
        Configured groups (see config.py).
    split_rules : list of SplitRule
        Configured split rules.

    Returns
    -------
    GeoDataFrame
        Same rows as ``parishes``, with one new column ``upper_district``
        giving the redesigned-district name for each parish.
    """
    out = parishes.merge(voters_by_parish[[parish_col, voters_col]], on=parish_col, how="left")
    if out[voters_col].isna().any():
        n = int(out[voters_col].isna().sum())
        logger.warning(
            "%d parishes have no registered_voters value; treating as zero. "
            "This may indicate stale election data vs current CAOP boundaries.",
            n,
        )
        out[voters_col] = out[voters_col].fillna(0)

    # Step 1: build the merge map from configured groups.
    merge_map: dict[str, str] = {}
    for grp in list(always_merged) + list(merge_groups):
        for member in grp.members:
            if member in merge_map:
                raise ValueError(
                    f"Distrito '{member}' assigned to multiple groups: "
                    f"'{merge_map[member]}' vs '{grp.name}'"
                )
            merge_map[member] = grp.name

    # Step 2: initial assignment - either a configured group or the original distrito name.
    out["upper_district"] = out[distrito_col].map(
        lambda d: merge_map.get(d, d)
    )

    # Step 3: for each split rule, override the assignment within that distrito.
    for rule in split_rules:
        target = rule.district
        affected = out[distrito_col] == target
        n_affected = int(affected.sum())
        if n_affected == 0:
            logger.warning(
                "Split rule for '%s' matched zero parishes; check spelling.",
                target,
            )
            continue

        sub_assignments = _split_distrito(
            parishes=out[affected],
            voters_col=voters_col,
            municipio_col=municipio_col,
            parish_col=parish_col,
            k=rule.k,
            random_state=rule.random_state,
            improve_iters=rule.improve_iters,
        )
        # Override upper_district for affected parishes.
        for parish_id, sub_idx in sub_assignments.items():
            out.loc[out[parish_col] == parish_id, "upper_district"] = (
                f"{target} {sub_idx + 1}"
            )

        logger.info(
            "Split distrito '%s' into %d sub-districts (%d parishes affected).",
            target, rule.k, n_affected,
        )

    return out


def _split_distrito(
    parishes: gpd.GeoDataFrame,
    voters_col: str,
    municipio_col: str,
    parish_col: str,
    k: int,
    random_state: int,
    improve_iters: int,
) -> dict[str, int]:
    """Split one distrito into k contiguous sub-districts at the
    *municipality* level, then propagate the assignment to parishes.

    Operating at the municipality level (rather than the parish level)
    matches client expectation: a municipality is the smallest unit that
    voters and politicians treat as electorally meaningful, and Lisboa
    and Porto are well-known for politically active municipalities. It
    also makes the algorithm 10x faster because there are 16-18
    municipalities in Lisboa/Porto vs hundreds of parishes.

    Returns
    -------
    dict
        parish_id -> sub-district index (0..k-1).
    """
    # Aggregate parishes to municipality level.
    muni = (
        parishes.dissolve(by=municipio_col, aggfunc={voters_col: "sum"})
        .reset_index()
    )
    muni = muni.rename(columns={municipio_col: "unit_id"})

    # Build adjacency at municipality level; no virtual bridges since
    # Lisboa and Porto distritos are mainland-contiguous.
    g = build_adjacency_graph(muni, parish_col="unit_id", bridge_components=False)

    # If for some reason the municipalities are not connected, fail loudly.
    if not nx.is_connected(g):
        comps = list(nx.connected_components(g))
        raise RuntimeError(
            f"Municipalities of distrito are not contiguous "
            f"(components: {[len(c) for c in comps]}). Cannot split."
        )

    # Run the balanced partition.
    region_assign = balanced_contiguous_partition(
        units=muni,
        adjacency=g,
        k=k,
        weight_col=voters_col,
        unit_col="unit_id",
        random_state=random_state,
        improve_iters=improve_iters,
        verbose=True,
    )

    # Map municipality -> region; then propagate to each parish.
    muni_to_region = region_assign.to_dict()
    out = {}
    for _, row in parishes.iterrows():
        out[row[parish_col]] = muni_to_region[row[municipio_col]]
    return out


def aggregate_district_voters(
    parish_assignments: gpd.GeoDataFrame,
    voters_col: str = "registered_voters",
    district_col: str = "upper_district",
) -> pd.DataFrame:
    """Sum registered voters per upper-tier district. Returns a DataFrame
    sorted alphabetically by district name."""
    out = (
        parish_assignments.groupby(district_col)[voters_col].sum()
        .reset_index()
        .sort_values(district_col)
        .reset_index(drop=True)
    )
    return out


def dissolve_to_districts(
    parish_assignments: gpd.GeoDataFrame,
    district_col: str = "upper_district",
    voters_col: str = "registered_voters",
) -> gpd.GeoDataFrame:
    """Dissolve parish geometries into upper-tier district polygons.

    Returns a GeoDataFrame with one row per district, geometry = union
    of parish polygons, plus aggregated registered_voters.
    """
    dissolved = parish_assignments.dissolve(
        by=district_col, aggfunc={voters_col: "sum"}
    ).reset_index()
    dissolved = dissolved[[district_col, voters_col, "geometry"]]
    return dissolved
