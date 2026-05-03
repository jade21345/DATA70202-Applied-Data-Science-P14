"""Spatial utilities.

Two responsibilities:

1. Build a Queen-style adjacency graph over a parish GeoDataFrame, with
   support for virtual bridges across small spatial gaps (necessary for
   archipelagos like the Azores).
2. A reusable balanced-partition routine: given a connected subgraph of
   parishes, produce ``k`` contiguous regions of approximately equal
   total population. Used to split Lisboa into 3 and Porto into 2.
"""
from __future__ import annotations

import logging
from typing import Sequence

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from shapely.strtree import STRtree

logger = logging.getLogger(__name__)


def build_adjacency_graph(
    parishes: gpd.GeoDataFrame,
    parish_col: str = "parish_id",
    bridge_components: bool = True,
) -> nx.Graph:
    """Construct a Queen-style adjacency graph over parishes.

    Two parishes are neighbours if their geometries share at least one
    boundary point (Queen contiguity, including corner contacts). The
    function uses a spatial index for performance: building over 3000+
    parishes runs in under a second.

    If ``bridge_components`` is True, isolated components (e.g. the nine
    Azorean islands) are connected by adding one virtual edge per
    component to the nearest parish in the largest component. This makes
    downstream graph algorithms behave sensibly across the archipelago.
    Bridges added this way are flagged with edge attribute
    ``virtual=True`` so callers can detect them.

    Parameters
    ----------
    parishes : GeoDataFrame
        Must contain ``parish_col`` and a geometry column. Index of the
        graph will be the values of ``parish_col``.
    parish_col : str
        Column to use as node identifiers. Should be unique.
    bridge_components : bool
        Whether to add virtual edges joining disconnected components.

    Returns
    -------
    networkx.Graph
        Undirected graph with nodes = parish IDs.
    """
    if parishes[parish_col].duplicated().any():
        raise ValueError(f"Duplicate values in {parish_col}; cannot build graph.")

    g = nx.Graph()
    g.add_nodes_from(parishes[parish_col].tolist())

    geoms = parishes.geometry.values
    ids = parishes[parish_col].values
    tree = STRtree(geoms)

    # For each parish, query the spatial index and check actual touches/intersects.
    for i, geom in enumerate(geoms):
        # STRtree.query returns indices into the original list.
        candidate_idx = tree.query(geom)
        for j in candidate_idx:
            if j <= i:
                continue
            if geoms[j].touches(geom) or geoms[j].intersects(geom):
                g.add_edge(ids[i], ids[j])

    if bridge_components:
        components = list(nx.connected_components(g))
        if len(components) > 1:
            logger.info(
                "Adjacency graph has %d disconnected components; "
                "adding virtual bridges.", len(components),
            )
            id_to_geom = dict(zip(ids, geoms))
            largest = max(components, key=len)
            for comp in components:
                if comp is largest:
                    continue
                # Find the closest pair (u in comp, v in largest).
                best_u, best_v, best_d = None, None, float("inf")
                for u in comp:
                    gu = id_to_geom[u]
                    # Limit comparison to a handful of candidates in the largest
                    # component using the spatial index for efficiency.
                    candidate_idx = tree.nearest(gu)
                    # tree.nearest returns one index; broaden by querying a bbox.
                    # For correctness keep it simple: iterate over the largest comp.
                    # (3000 parishes; this loop runs at most ~10 times -> ~30k ops.)
                    for v in largest:
                        d = gu.distance(id_to_geom[v])
                        if d < best_d:
                            best_d, best_u, best_v = d, u, v
                if best_u is not None:
                    g.add_edge(best_u, best_v, virtual=True, distance=best_d)
                    logger.debug(
                        "Bridged component (size %d) to main via %s <-> %s (d=%.1f)",
                        len(comp), best_u, best_v, best_d,
                    )

    return g


def is_subgraph_connected(g: nx.Graph, nodes: Sequence) -> bool:
    """Return True iff the subgraph of ``g`` induced by ``nodes`` is connected."""
    if not nodes:
        return True
    sub = g.subgraph(nodes)
    return nx.is_connected(sub)


def balanced_contiguous_partition(
    units: pd.DataFrame,
    adjacency: nx.Graph,
    k: int,
    weight_col: str = "registered_voters",
    unit_col: str = "unit_id",
    random_state: int = 0,
    improve_iters: int = 10000,
    verbose: bool = False,
) -> pd.Series:
    """Partition a connected set of spatial units into k contiguous regions
    of approximately equal total weight.

    Two-phase algorithm:
      Phase 1 (region growing): pick k seeds, grow each by repeatedly
      adopting an unassigned neighbour that minimises the weight of the
      smallest region (greedy load-balancing).
      Phase 2 (local search): for ``improve_iters`` iterations, propose
      a single boundary unit move from one region to a neighbouring
      region; accept iff (a) it strictly reduces the sum of absolute
      deviations from the target weight, and (b) both source and
      destination remain contiguous.

    This is the same family of algorithm used in your existing
    Assignment1 notebook, with two differences: it operates on any unit
    (not specifically municipalities), and it uses a deterministic seed
    selection so the same input gives the same output.

    Parameters
    ----------
    units : DataFrame
        One row per spatial unit (e.g. municipality, parish). Must
        include ``unit_col`` and ``weight_col``.
    adjacency : networkx.Graph
        Adjacency graph over the units; nodes must match unit_col values.
    k : int
        Number of regions to produce.
    weight_col : str
        Column to balance (typically registered voters).
    random_state : int
        Seed for tie-breaking randomness.
    improve_iters : int
        Number of local-search iterations in phase 2.

    Returns
    -------
    pandas.Series
        Indexed by unit_col, values are integers 0..k-1 indicating the
        region each unit belongs to.
    """
    rng = np.random.default_rng(random_state)
    unit_ids = units[unit_col].tolist()
    weights = dict(zip(unit_ids, units[weight_col].astype(float)))

    if not nx.is_connected(adjacency.subgraph(unit_ids)):
        raise ValueError(
            f"Cannot partition: adjacency subgraph over the supplied units is "
            f"not connected ({nx.number_connected_components(adjacency.subgraph(unit_ids))} components)."
        )
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if k > len(unit_ids):
        raise ValueError(f"k={k} exceeds number of units {len(unit_ids)}")

    # ---- Phase 1: choose seeds and grow regions ----
    # Seed strategy: pick k units that are pairwise far apart in the graph
    # (graph diameter sampling). This produces visually well-separated regions.
    seeds = _select_distant_seeds(adjacency.subgraph(unit_ids), k, rng)
    assignment = {u: -1 for u in unit_ids}
    for r, seed in enumerate(seeds):
        assignment[seed] = r

    region_weight = {r: weights[seeds[r]] for r in range(k)}
    unassigned = set(unit_ids) - set(seeds)

    # Greedy growth: at each step, look at all (unassigned u, region r)
    # pairs where u is adjacent to some unit already in r, and pick the
    # combination that gives the smallest *largest* region weight.
    while unassigned:
        candidates = []
        for u in unassigned:
            neighbour_regions = {
                assignment[v] for v in adjacency.neighbors(u)
                if assignment.get(v, -1) != -1
            }
            for r in neighbour_regions:
                candidates.append((u, r))

        if not candidates:
            # The remaining unassigned set has no boundary with any region.
            # This can happen when virtual bridges exist but the bridge
            # endpoints are still unassigned. Force-assign each isolate to
            # the lightest neighbouring region in the unrestricted graph.
            for u in list(unassigned):
                neigh = list(adjacency.neighbors(u))
                if neigh:
                    # Pick lightest region among any neighbour (assigned or not).
                    assigned_neigh = [v for v in neigh if assignment[v] != -1]
                    if assigned_neigh:
                        target = min(assigned_neigh, key=lambda v: region_weight[assignment[v]])
                        r = assignment[target]
                    else:
                        r = min(region_weight, key=region_weight.get)
                else:
                    r = min(region_weight, key=region_weight.get)
                assignment[u] = r
                region_weight[r] += weights[u]
                unassigned.discard(u)
            break

        # Pick the (u, r) that, after assigning u to r, results in the
        # smallest max-region-weight (greedy load-balancing).
        def cost(pair):
            u, r = pair
            return region_weight[r] + weights[u]
        u_best, r_best = min(candidates, key=cost)
        assignment[u_best] = r_best
        region_weight[r_best] += weights[u_best]
        unassigned.discard(u_best)

    # ---- Phase 2: local search swaps ----
    target = sum(weights.values()) / k

    def total_deviation(assign_map: dict) -> float:
        rw = {r: 0.0 for r in range(k)}
        for u, r in assign_map.items():
            rw[r] += weights[u]
        return sum(abs(w - target) for w in rw.values())

    current_dev = total_deviation(assignment)

    sub_g = adjacency.subgraph(unit_ids)

    accepted = 0
    for it in range(improve_iters):
        # Pick a random unit on a region boundary.
        u = unit_ids[rng.integers(0, len(unit_ids))]
        r_curr = assignment[u]
        neighbour_regions = {
            assignment[v] for v in sub_g.neighbors(u) if assignment[v] != r_curr
        }
        if not neighbour_regions:
            continue
        r_new = rng.choice(list(neighbour_regions))

        # Contiguity preservation: removing u from r_curr must leave r_curr connected.
        remaining = [n for n, rr in assignment.items() if rr == r_curr and n != u]
        if remaining and not is_subgraph_connected(sub_g, remaining):
            continue

        # Tentatively move and check if deviation improved.
        old_curr_w = region_weight[r_curr]
        old_new_w = region_weight[r_new]
        new_dev = (
            current_dev
            - abs(old_curr_w - target) - abs(old_new_w - target)
            + abs(old_curr_w - weights[u] - target) + abs(old_new_w + weights[u] - target)
        )
        if new_dev < current_dev - 1e-9:
            assignment[u] = r_new
            region_weight[r_curr] -= weights[u]
            region_weight[r_new] += weights[u]
            current_dev = new_dev
            accepted += 1

    if verbose:
        logger.info(
            "balanced_contiguous_partition: %d swaps accepted out of %d iterations; "
            "final per-region weights: %s (target=%.0f)",
            accepted, improve_iters,
            {r: int(w) for r, w in region_weight.items()}, target,
        )

    return pd.Series(assignment, name="region")


def _select_distant_seeds(g: nx.Graph, k: int, rng: np.random.Generator) -> list:
    """Pick k seed nodes that are pairwise far apart (graph distance).

    Algorithm: pick a random first seed; for each subsequent seed, pick
    the node with the largest minimum graph distance to the existing seeds.
    Equivalent to k-means++ on graph distance.
    """
    nodes = list(g.nodes)
    seeds = [nodes[rng.integers(0, len(nodes))]]
    while len(seeds) < k:
        # Compute distance from each node to its closest existing seed.
        dist = {n: float("inf") for n in nodes}
        for s in seeds:
            sp = nx.single_source_shortest_path_length(g, s)
            for n, d in sp.items():
                if d < dist[n]:
                    dist[n] = d
        # Pick the node with the largest minimum distance.
        best = max(nodes, key=lambda n: dist[n])
        seeds.append(best)
    return seeds
