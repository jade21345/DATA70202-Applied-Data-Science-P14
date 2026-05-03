"""Full apportionment + allocation pipeline.

Runs the entire model end-to-end:
  1. Loads cleaned data produced by 01_prepare_data.py
  2. Redesigns the upper tier
  3. Hamilton + tier-split (already validated against client)
  4. Aggregates parish votes to upper-tier
  5. D'Hondt allocates party-list seats per upper-tier district
  6. Lower-tier districting (single-member zones inside each upper district)
  7. Determines lower-tier winners (party-vote approximation)
  8. Combines into national party totals
  9. Exports all frontend-ready files

Usage:
    python scripts/04_run_full_pipeline.py
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

from apportionment import (  # noqa: E402
    allocate_dhondt_by_district, allocate_hamilton, split_tiers,
)
from config import load_config  # noqa: E402
from io_utils import write_csv, write_geojson  # noqa: E402
from lower_districting import assign_lower_districts  # noqa: E402
from results import combine_results, parliament_to_json  # noqa: E402
from spatial_utils import build_adjacency_graph  # noqa: E402
from upper_redesign import redesign_upper_tier  # noqa: E402
from validation import (  # noqa: E402
    require_columns, require_dhondt_seat_counts_match,
    require_hamilton_total, require_one_to_one_assignment,
    require_tier_split_consistency,
)
from vote_aggregation import (  # noqa: E402
    aggregate_votes_to_lower, aggregate_votes_to_upper, lower_tier_winners,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run full apportionment + allocation pipeline.")
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("04_run_full_pipeline")

    cfg = load_config(args.config)
    log.info("Scenario: %s (%s)", cfg.scenario_name, cfg.scenario_id)

    out_tables = ROOT / cfg.paths["outputs_dir"] / "tables"
    out_geojson = ROOT / cfg.paths["outputs_dir"] / "geojson"
    out_json = ROOT / cfg.paths["outputs_dir"] / "json"
    out_docs = ROOT / cfg.paths["outputs_dir"] / "documentation"
    for d in (out_tables, out_geojson, out_json, out_docs):
        d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Load inputs
    # ------------------------------------------------------------------
    parishes = gpd.read_file(cfg.path("clean_parishes_gpkg"), layer="parishes")
    require_columns(
        parishes, ["parish_id", "municipality_id", "distrito_ilha", "municipio", "geometry"],
        "parishes",
    )
    log.info("Loaded %d parishes.", len(parishes))

    muni_voters = pd.read_csv(cfg.path("clean_municipality_voters_csv"), dtype={"municipality_id": str})
    votes_long = pd.read_csv(
        cfg.path("clean_election_csv"),
        dtype={"parish_id": str, "municipality_id": str},
    )
    parties_meta = pd.read_csv(ROOT / "config" / "parties.csv")

    log.info("Loaded %d municipalities, %d vote rows, %d known parties.",
             len(muni_voters), len(votes_long), len(parties_meta))

    # ------------------------------------------------------------------
    # 2. Distribute municipality voters to parishes (for redesign weights)
    # ------------------------------------------------------------------
    parishes_voters = _allocate_voters_to_parishes(parishes, muni_voters)

    # ------------------------------------------------------------------
    # 3. Upper-tier redesign
    # ------------------------------------------------------------------
    log.info("Applying upper-tier redesign...")
    parishes_assigned = redesign_upper_tier(
        parishes=parishes,
        voters_by_parish=parishes_voters[["parish_id", "registered_voters"]],
        merge_groups=list(cfg.upper_tier.merge_groups),
        always_merged=list(cfg.upper_tier.always_merged),
        split_rules=list(cfg.upper_tier.split_rules),
        parish_col="parish_id",
        distrito_col="distrito_ilha",
        municipio_col="municipio",
        voters_col="registered_voters",
    )
    require_one_to_one_assignment(parishes_assigned, "parish_id", "upper_district", "upper_district")

    # ------------------------------------------------------------------
    # 4. Authoritative district voter totals (via municipality_id)
    # ------------------------------------------------------------------
    muni_to_district = (
        parishes_assigned[["municipality_id", "upper_district"]]
        .drop_duplicates("municipality_id")
    )
    district_voters = (
        muni_to_district.merge(muni_voters, on="municipality_id")
        .groupby("upper_district")["registered_voters"].sum()
        .reset_index()
        .sort_values("upper_district")
        .reset_index(drop=True)
    )

    # ------------------------------------------------------------------
    # 5. Hamilton + tier split
    # ------------------------------------------------------------------
    hamilton = allocate_hamilton(district_voters, cfg.total_seats)
    require_hamilton_total(hamilton, cfg.total_seats)
    tier = split_tiers(hamilton, cfg.party_list_ratio, rounding=cfg.tier_split_rounding)
    require_tier_split_consistency(tier)
    log.info("Hamilton: %d total; tier split: %d party-list + %d single-member (%s rounding).",
             cfg.total_seats, tier["party_list_seats"].sum(),
             tier["single_member_seats"].sum(), cfg.tier_split_rounding)

    write_csv(parishes_assigned[
        ["parish_id", "municipality_id", "municipio", "distrito_ilha", "upper_district"]
    ], out_tables / "upper_district_membership.csv")
    write_csv(hamilton, out_tables / "hamilton_allocation.csv")
    write_csv(tier, out_tables / "tier_split.csv")

    # ------------------------------------------------------------------
    # 6. Vote aggregation to upper tier
    # ------------------------------------------------------------------
    parish_to_upper = parishes_assigned[["parish_id", "municipality_id", "upper_district"]]
    votes_upper = aggregate_votes_to_upper(votes_long, parish_to_upper)
    log.info("Aggregated votes into upper-tier: %d (district, party) rows", len(votes_upper))
    write_csv(votes_upper, out_tables / "upper_district_party_votes.csv")

    # ------------------------------------------------------------------
    # 7. D'Hondt party-list allocation per district
    # ------------------------------------------------------------------
    dhondt = allocate_dhondt_by_district(
        votes_upper, tier[["upper_district", "party_list_seats"]],
    )
    require_dhondt_seat_counts_match(dhondt, tier)
    log.info("D'Hondt allocated %d party-list seats across %d districts.",
             dhondt["allocated_seats"].sum(), dhondt["upper_district"].nunique())
    write_csv(dhondt, out_tables / "dhondt_results_by_district.csv")

    # ------------------------------------------------------------------
    # 8. Build adjacency graph for lower-tier districting
    # ------------------------------------------------------------------
    log.info("Building parish adjacency graph (with virtual island bridges)...")
    adjacency = build_adjacency_graph(
        parishes, parish_col="parish_id", bridge_components=True,
    )

    # ------------------------------------------------------------------
    # 9. Lower-tier districting
    # ------------------------------------------------------------------
    log.info("Running lower-tier districting (seed_strategy=%s)...",
             cfg.lower_tier.seed_strategy)
    seeds = None
    if cfg.lower_tier.seed_strategy == "client_provided":
        # Load from a CSV the client (or simulator) supplies. If absent,
        # fall back to the algorithmic strategy with a clear log message.
        seeds_csv = ROOT / "data_clean" / "seed_parishes.csv"
        if seeds_csv.exists():
            sdf = pd.read_csv(seeds_csv, dtype={"parish_id": str})
            seeds = sdf.groupby("upper_district")["parish_id"].apply(list).to_dict()
            log.info("Loaded client-provided seeds for %d districts.", len(seeds))
            fallback_strategy = "largest_population"  # only used for missing districts
        else:
            log.warning(
                "client_provided seeds requested but %s not found; "
                "falling back to algorithmic seed selection (largest_population).",
                seeds_csv,
            )
            fallback_strategy = "largest_population"
    else:
        fallback_strategy = cfg.lower_tier.seed_strategy

    lower_membership, lower_diag = assign_lower_districts(
        parishes=parishes_assigned,
        parish_voters=parishes_voters,
        upper_assignments=parishes_assigned,
        tier_split=tier,
        adjacency=adjacency,
        seeds=seeds,
        seed_strategy=fallback_strategy,
        skip_districts=cfg.lower_tier.skip_districts,
        tolerance=cfg.lower_tier.tolerance,
        max_iterations=cfg.lower_tier.max_iterations,
        rng_seed=42,
    )
    log.info("Created %d lower-tier districts across %d upper districts.",
             lower_membership["lower_district"].nunique(),
             lower_membership["upper_district"].nunique())
    write_csv(lower_membership, out_tables / "lower_district_membership.csv")
    write_csv(lower_diag, out_tables / "lower_district_diagnostics.csv")

    # ------------------------------------------------------------------
    # 10. Aggregate votes to lower-tier and pick winners
    # ------------------------------------------------------------------
    parish_to_municipality = parishes_assigned[["parish_id", "municipality_id"]]
    votes_lower = aggregate_votes_to_lower(
        votes_long, lower_membership, parish_to_municipality,
        municipality_fallback=True,
    )
    write_csv(votes_lower, out_tables / "lower_district_party_votes.csv")

    sm_winners = lower_tier_winners(votes_lower, all_districts=lower_diag)
    log.info("Determined winners for %d single-member districts.", len(sm_winners))
    write_csv(sm_winners, out_tables / "single_member_winners.csv")

    # ------------------------------------------------------------------
    # 11. Combine results
    # ------------------------------------------------------------------
    breakdown, final = combine_results(dhondt, sm_winners, parties_meta)
    write_csv(breakdown, out_tables / "party_seat_breakdown.csv")
    write_csv(final, out_tables / "final_party_seat_results.csv")

    parliament = parliament_to_json(final, cfg.total_seats)
    with (out_json / "final_party_seat_results.json").open("w", encoding="utf-8") as f:
        json.dump(parliament, f, ensure_ascii=False, indent=2)
    log.info("Wrote parliament JSON: %s", out_json / "final_party_seat_results.json")

    # ------------------------------------------------------------------
    # 12. GeoJSON for the static frontend
    # ------------------------------------------------------------------
    # Upper districts.
    dissolved_upper = parishes_assigned.dissolve(by="upper_district").reset_index()
    dissolved_upper = dissolved_upper[["upper_district", "geometry"]]
    dissolved_upper = (
        dissolved_upper
        .merge(district_voters, on="upper_district")
        .merge(hamilton[["upper_district", "total_mandates"]], on="upper_district")
        .merge(tier[["upper_district", "party_list_seats", "single_member_seats"]],
               on="upper_district")
    )
    dissolved_upper = gpd.GeoDataFrame(dissolved_upper, crs=parishes_assigned.crs)
    write_geojson(dissolved_upper, out_geojson / "upper_districts.geojson", cfg.export_crs)

    # Lower districts (with winner attribute for direct rendering).
    parish_to_lower = lower_membership.merge(
        parishes_assigned[["parish_id", "geometry"]], on="parish_id", how="left",
    )
    parish_to_lower = gpd.GeoDataFrame(parish_to_lower, crs=parishes_assigned.crs)
    dissolved_lower = parish_to_lower.dissolve(by="lower_district").reset_index()
    dissolved_lower = (
        dissolved_lower[["lower_district", "geometry"]]
        .merge(lower_diag, on="lower_district")
        .merge(sm_winners, on=["lower_district", "parent_upper_district"])
        .merge(parties_meta[["party_id", "short_name", "color"]],
               left_on="winning_party", right_on="party_id", how="left")
    )
    dissolved_lower = gpd.GeoDataFrame(dissolved_lower, crs=parishes_assigned.crs)
    write_geojson(dissolved_lower, out_geojson / "lower_districts.geojson", cfg.export_crs)

    # ------------------------------------------------------------------
    # 13. Documentation
    # ------------------------------------------------------------------
    # Save a copy of the scenario config and a data dictionary.
    with (out_docs / "scenario_config.json").open("w", encoding="utf-8") as f:
        json.dump(cfg.raw, f, ensure_ascii=False, indent=2)
    _write_data_dictionary(out_docs / "data_dictionary.csv")

    log.info("Pipeline complete. All outputs are under: %s", ROOT / cfg.paths["outputs_dir"])
    return 0


def _allocate_voters_to_parishes(
    parishes: gpd.GeoDataFrame, muni_voters: pd.DataFrame,
) -> pd.DataFrame:
    """Split each municipality's voters among its parishes by area share."""
    p = parishes[["parish_id", "municipality_id", "geometry"]].copy()
    p["area"] = p.geometry.area
    muni_area = p.groupby("municipality_id")["area"].sum().rename("muni_area")
    p = p.merge(muni_area, on="municipality_id", how="left")
    p["area_share"] = p["area"] / p["muni_area"]
    p = p.merge(muni_voters[["municipality_id", "registered_voters"]],
                on="municipality_id", how="left")
    p["registered_voters"] = (p["registered_voters"] * p["area_share"]).round().astype(int)
    return p[["parish_id", "municipality_id", "registered_voters"]]


def _write_data_dictionary(path: Path) -> None:
    """Emit a CSV that explains every output column the frontend will see."""
    rows = [
        ("upper_district", "Name of redesigned upper-tier district", "string"),
        ("lower_district", "Name of single-member sub-district", "string"),
        ("parent_upper_district", "Upper-tier district containing this lower-tier district", "string"),
        ("parish_id", "6-digit DICOFRE code", "string"),
        ("municipality_id", "4-digit DTMN code (DICOFRE prefix)", "string"),
        ("party", "Party identifier as appearing in the official source", "string"),
        ("votes", "Vote count", "integer"),
        ("registered_voters", "Eligible voters registered in this unit", "integer"),
        ("voter_share", "Share of national registered voters", "float [0,1]"),
        ("quota", "Hamilton quota (voter_share * total_seats)", "float"),
        ("floor_seats", "Floor of the Hamilton quota", "integer"),
        ("remainder", "Decimal remainder of the Hamilton quota", "float"),
        ("extra_seat", "1 if district received an extra seat from largest-remainder", "0 or 1"),
        ("total_mandates", "Total seats allocated to district by Hamilton", "integer"),
        ("party_list_seats", "Multi-member (D'Hondt) seats", "integer"),
        ("single_member_seats", "Lower-tier (winner-take-all) seats", "integer"),
        ("allocated_seats", "Seats won by a party in a district via D'Hondt", "integer"),
        ("winning_party", "Party with most votes in single-member district", "string"),
        ("margin", "Winning_votes - runner_up_votes", "integer"),
        ("margin_pct", "Margin as share of total district votes", "float"),
        ("target_voters", "Lower-tier target electorate (= upper voters / U_i)", "integer"),
        ("deviation", "(actual - target) / target", "float"),
        ("is_contiguous", "Whether the district is territorially contiguous", "boolean"),
    ]
    pd.DataFrame(rows, columns=["field", "description", "type"]).to_csv(
        path, index=False, encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
