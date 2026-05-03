"""Lower-tier (single-member) districting.

For each redesigned upper-tier district, partition the parishes into
``U_i`` contiguous lower-tier districts of approximately equal
registered voters. Lower-tier districts are *strictly nested* inside
upper-tier districts (rule 3 of the Methodology) — a hard constraint,
not a soft penalty.

Algorithm: seed-based region growing with priority-queue greedy.

  1. For each upper-tier district independently:
     a. Collect its parishes and their voter counts.
     b. Determine seed parishes: either client-provided, or computed
        via a fallback strategy (largest-population, k-means++).
     c. Each lower-tier district starts as a single-parish region
        seeded by one seed parish.
     d. Maintain a priority queue of (region_population_deficit, region,
        candidate_neighbour). At each step pop the most-underweight
        region's best candidate and assign it.
     e. When all parishes are assigned, perform optional repair swaps
        to reduce voter deviation.

  2. The output is a parish -> lower_district_id mapping plus diagnostics.

Lisboa 2 etc. example: if upper-tier 'Lisboa 2' has U_i = 5, this
module will produce 5 lower-tier districts named 'Lisboa 2 - SM 1' ...
'Lisboa 2 - SM 5' that together tile Lisboa 2's territory.
"""
from __future__ import annotations

import heapq
import logging
from typing import Iterable

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def assign_lower_districts(
    parishes: gpd.GeoDataFrame,
    parish_voters: pd.DataFrame,
    upper_assignments: pd.DataFrame,
    tier_split: pd.DataFrame,
    adjacency: nx.Graph,
    seeds: dict[str, list[str]] | None = None,
    seed_strategy: str = "largest_population",
    skip_districts: Iterable[str] = (),
    tolerance: float = 0.10,
    max_iterations: int = 50000,
    parish_col: str = "parish_id",
    voters_col: str = "registered_voters",
    upper_col: str = "upper_district",
    rng_seed: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Partition each upper-tier district into U_i lower-tier districts.

    Parameters
    ----------
    parishes : GeoDataFrame
        One row per parish; must contain parish_col and geometry.
    parish_voters : DataFrame
        parish_col -> registered_voters (numeric). Source of weights.
    upper_assignments : DataFrame
        parish_col -> upper_district (output of upper_redesign).
    tier_split : DataFrame
        upper_district -> single_member_seats (= U_i for that district).
    adjacency : networkx.Graph
        Nationwide parish adjacency graph.
    seeds : dict, optional
        upper_district -> [parish_id, parish_id, ...] seed list.
        If None or missing for a district, ``seed_strategy`` is used.
    seed_strategy : str
        Fallback when seeds for a district are not supplied:
          'largest_population': pick the U_i parishes with most voters,
                                spread by graph distance
          'kmeans_pp':         k-means++ in parish-centroid space
    skip_districts : iterable
        Upper-tier districts to skip (no lower-tier created).
        Useful for the islands if the team decides not to apply
        single-member districting there.
    tolerance : float
        Soft target: |voters - target| / target should be below this.
        Used only as a diagnostic; the algorithm always returns a result.
    max_iterations : int
        Cap on the swap-repair loop iterations.
    rng_seed : int
        Random seed for any tie-breaking randomness.

    Returns
    -------
    membership : DataFrame
        Columns: parish_id, upper_district, lower_district, parent_upper_district.
        One row per parish that is part of a lower-tier district.
        Parishes in skip_districts are absent.
    diagnostics : DataFrame
        One row per lower_district with columns:
        lower_district, parent_upper_district, n_parishes, registered_voters,
        target_voters, deviation, is_contiguous, notes.
    """
    rng = np.random.default_rng(rng_seed)
    seeds = seeds or {}

    # Merge inputs.
    work = (
        parishes[[parish_col]]
        .merge(parish_voters[[parish_col, voters_col]], on=parish_col, how="left")
        .merge(upper_assignments[[parish_col, upper_col]], on=parish_col, how="left")
    )
    work[voters_col] = work[voters_col].fillna(0).astype(int)

    seats_lookup = dict(zip(tier_split[upper_col], tier_split["single_member_seats"]))

    membership_rows = []
    diag_rows = []

    skip_set = set(skip_districts)

    for upper_district, sub in work.groupby(upper_col):
        if upper_district in skip_set:
            logger.info("Skipping lower-tier districting for '%s' (configured).", upper_district)
            continue

        u_i = int(seats_lookup.get(upper_district, 0))
        if u_i == 0:
            logger.info(
                "Upper district '%s' has U_i = 0 (no single-member seats); skipping.",
                upper_district,
            )
            continue
        if u_i == 1:
            # Trivial case: the whole upper district is one lower district.
            sd_name = f"{upper_district} - SM 1"
            for _, row in sub.iterrows():
                membership_rows.append({
                    "parish_id": row[parish_col],
                    "upper_district": upper_district,
                    "lower_district": sd_name,
                    "parent_upper_district": upper_district,
                })
            voters_total = int(sub[voters_col].sum())
            diag_rows.append({
                "lower_district": sd_name,
                "parent_upper_district": upper_district,
                "n_parishes": len(sub),
                "registered_voters": voters_total,
                "target_voters": voters_total,
                "deviation": 0.0,
                "is_contiguous": True,
                "notes": "single lower-tier district equals upper-tier",
            })
            continue

        # Multi-region case: run the algorithm.
        local_seeds = seeds.get(upper_district)
        sub_membership, sub_diag = _grow_regions(
            sub_parishes=sub,
            adjacency=adjacency,
            u_i=u_i,
            upper_district=upper_district,
            local_seeds=local_seeds,
            seed_strategy=seed_strategy,
            tolerance=tolerance,
            max_iterations=max_iterations,
            voters_col=voters_col,
            parish_col=parish_col,
            rng=rng,
        )
        membership_rows.extend(sub_membership)
        diag_rows.extend(sub_diag)

    membership = pd.DataFrame(membership_rows)
    diagnostics = pd.DataFrame(diag_rows)
    return membership, diagnostics


def _grow_regions(
    sub_parishes: pd.DataFrame,
    adjacency: nx.Graph,
    u_i: int,
    upper_district: str,
    local_seeds: list | None,
    seed_strategy: str,
    tolerance: float,
    max_iterations: int,
    voters_col: str,
    parish_col: str,
    rng: np.random.Generator,
) -> tuple[list, list]:
    """Run the seed-based region-growing algorithm inside one upper-tier district."""
    parish_ids = sub_parishes[parish_col].tolist()
    voters = dict(zip(sub_parishes[parish_col], sub_parishes[voters_col].astype(int)))
    total_voters = sum(voters.values())
    target = total_voters / u_i

    sub_g = adjacency.subgraph(parish_ids)
    if not nx.is_connected(sub_g):
        # Multi-component upper-tier district; this happens for islands.
        # We still run the algorithm but log a warning - regions may end
        # up not contiguous within their component.
        n_components = nx.number_connected_components(sub_g)
        logger.warning(
            "Upper district '%s' has %d disconnected components; "
            "lower-tier districts may not be perfectly contiguous.",
            upper_district, n_components,
        )

    # Determine seeds.
    if local_seeds:
        if len(local_seeds) != u_i:
            raise ValueError(
                f"Upper district '{upper_district}' needs {u_i} seeds; "
                f"got {len(local_seeds)} from configuration."
            )
        if not all(s in parish_ids for s in local_seeds):
            bad = [s for s in local_seeds if s not in parish_ids]
            raise ValueError(
                f"Some configured seeds for '{upper_district}' are not "
                f"parishes of that district: {bad}"
            )
        seeds = list(local_seeds)
    else:
        seeds = _pick_seeds_by_strategy(
            parish_ids, voters, sub_g, u_i, seed_strategy, rng,
        )

    # Initialise each region with one seed.
    assignment = {p: None for p in parish_ids}
    region_voters = [0] * u_i
    for r, seed in enumerate(seeds):
        assignment[seed] = r
        region_voters[r] = voters[seed]

    unassigned = set(parish_ids) - set(seeds)

    # Greedy growth: at each step, find the most under-target region and
    # assign it the neighbouring unassigned parish that brings it closest
    # to target.
    while unassigned:
        # Build candidate frontier: (region, candidate_parish)
        # for every region with at least one boundary neighbour.
        best_pair = None
        best_score = float("inf")
        # Pick the region with the largest deficit (target - current).
        # Tie: pick the region with the smallest current population.
        deficits = [
            (target - region_voters[r], r) for r in range(u_i)
        ]
        deficits.sort(reverse=True)  # largest deficit first

        chosen_region = None
        chosen_parish = None
        for deficit, r in deficits:
            # Find unassigned neighbours of region r.
            region_parishes = [p for p, rr in assignment.items() if rr == r]
            frontier = set()
            for rp in region_parishes:
                for nb in sub_g.neighbors(rp):
                    if assignment[nb] is None:
                        frontier.add(nb)
            if not frontier:
                continue
            # Pick the candidate that brings region voter total closest to target.
            best_cand = None
            best_diff = float("inf")
            for cand in frontier:
                new_total = region_voters[r] + voters[cand]
                diff = abs(new_total - target)
                if diff < best_diff or (diff == best_diff and voters[cand] < voters.get(best_cand, float("inf"))):
                    best_diff = diff
                    best_cand = cand
            if best_cand is not None:
                chosen_region = r
                chosen_parish = best_cand
                break

        if chosen_parish is None:
            # No region has a frontier into unassigned (e.g. islands).
            # Force-assign each remaining parish to its lightest region
            # neighbour (or the lightest region overall if isolated).
            for u in list(unassigned):
                neigh_regions = {
                    assignment[v] for v in sub_g.neighbors(u)
                    if assignment.get(v) is not None
                }
                if neigh_regions:
                    r = min(neigh_regions, key=lambda rr: region_voters[rr])
                else:
                    r = int(np.argmin(region_voters))
                assignment[u] = r
                region_voters[r] += voters[u]
                unassigned.discard(u)
            break

        assignment[chosen_parish] = chosen_region
        region_voters[chosen_region] += voters[chosen_parish]
        unassigned.discard(chosen_parish)

    # Repair phase: small swap loop to reduce deviation.
    region_voters, assignment = _repair_swaps(
        assignment, voters, sub_g, u_i, target, max_iterations, rng,
    )

    # Build outputs.
    membership = []
    for p, r in assignment.items():
        sd_name = f"{upper_district} - SM {r + 1}"
        membership.append({
            "parish_id": p,
            "upper_district": upper_district,
            "lower_district": sd_name,
            "parent_upper_district": upper_district,
        })

    diagnostics = []
    for r in range(u_i):
        sd_name = f"{upper_district} - SM {r + 1}"
        members = [p for p, rr in assignment.items() if rr == r]
        v_sum = region_voters[r]
        deviation = (v_sum - target) / target if target > 0 else 0.0
        sg = sub_g.subgraph(members)
        contiguous = nx.is_connected(sg) if members else True
        notes = []
        if abs(deviation) > tolerance:
            notes.append(f"deviation {deviation:+.1%} exceeds tolerance {tolerance:.0%}")
        if not contiguous:
            notes.append("not contiguous")
        diagnostics.append({
            "lower_district": sd_name,
            "parent_upper_district": upper_district,
            "n_parishes": len(members),
            "registered_voters": int(v_sum),
            "target_voters": int(round(target)),
            "deviation": round(float(deviation), 4),
            "is_contiguous": bool(contiguous),
            "notes": "; ".join(notes) if notes else "",
        })

    return membership, diagnostics


def _repair_swaps(
    assignment: dict,
    voters: dict,
    sub_g: nx.Graph,
    u_i: int,
    target: float,
    max_iterations: int,
    rng: np.random.Generator,
    no_improve_patience: int = 1000,
) -> tuple[list[int], dict]:
    """Improve the assignment by single-parish boundary swaps.

    Stops early after ``no_improve_patience`` consecutive iterations
    without an accepted swap. This dramatically reduces runtime on
    small districts where most iterations are no-ops.
    """
    region_voters = [0] * u_i
    for p, r in assignment.items():
        region_voters[r] += voters[p]

    parish_list = list(assignment.keys())
    iters_since_improve = 0

    for _ in range(max_iterations):
        if iters_since_improve >= no_improve_patience:
            break
        iters_since_improve += 1

        u = parish_list[rng.integers(0, len(parish_list))]
        r_curr = assignment[u]
        neigh_r = {assignment[v] for v in sub_g.neighbors(u) if assignment[v] != r_curr}
        if not neigh_r:
            continue
        r_new = int(rng.choice(list(neigh_r)))

        # Cheap check first: does this swap reduce deviation?
        delta = (
            -abs(region_voters[r_curr] - target) - abs(region_voters[r_new] - target)
            + abs(region_voters[r_curr] - voters[u] - target)
            + abs(region_voters[r_new] + voters[u] - target)
        )
        if delta >= -1e-9:
            continue  # not improving, skip the expensive contiguity check

        # Expensive check: removing u from r_curr must leave it connected.
        remaining = [p for p, rr in assignment.items() if rr == r_curr and p != u]
        if remaining and not nx.is_connected(sub_g.subgraph(remaining)):
            continue

        assignment[u] = r_new
        region_voters[r_curr] -= voters[u]
        region_voters[r_new] += voters[u]
        iters_since_improve = 0

    return region_voters, assignment


def _pick_seeds_by_strategy(
    parish_ids: list,
    voters: dict,
    sub_g: nx.Graph,
    u_i: int,
    strategy: str,
    rng: np.random.Generator,
) -> list:
    """Choose U_i seed parishes when none are configured.

    'largest_population': among the top 3*U_i populous parishes, pick
        U_i pairwise-distant via graph BFS. Avoids clustering seeds in
        urban cores when the upper-tier district has many cities.
    """
    if strategy != "largest_population":
        raise NotImplementedError(
            f"Seed strategy '{strategy}' not implemented; "
            "configured 'largest_population' is the supported fallback."
        )

    sorted_p = sorted(parish_ids, key=lambda p: voters[p], reverse=True)
    candidate_pool = sorted_p[: max(u_i, min(3 * u_i, len(sorted_p)))]

    # Greedy farthest-first selection over the candidate pool.
    seeds = [candidate_pool[0]]
    while len(seeds) < u_i:
        best, best_d = None, -1
        for p in candidate_pool:
            if p in seeds:
                continue
            min_d = float("inf")
            for s in seeds:
                try:
                    d = nx.shortest_path_length(sub_g, p, s)
                except nx.NetworkXNoPath:
                    d = float("inf")
                if d < min_d:
                    min_d = d
            if min_d > best_d:
                best_d = min_d
                best = p
        if best is None:
            # Fallback: just take next most populous unused parish.
            for p in candidate_pool:
                if p not in seeds:
                    best = p
                    break
        seeds.append(best)
    return seeds
