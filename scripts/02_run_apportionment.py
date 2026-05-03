"""Apportionment pipeline.

Reads the cleaned data produced by 01_prepare_data.py, applies the
upper-tier redesign (merging + splitting), runs Hamilton allocation,
splits each district into party-list and single-member seats, and emits
the canonical CSV/GeoJSON outputs that the static frontend will consume.

Run from the project root:
    python scripts/02_run_apportionment.py
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

from apportionment import allocate_hamilton, split_tiers  # noqa: E402
from config import load_config  # noqa: E402
from io_utils import write_csv, write_geojson  # noqa: E402
from upper_redesign import (  # noqa: E402
    aggregate_district_voters,
    dissolve_to_districts,
    redesign_upper_tier,
)
from validation import (  # noqa: E402
    require_columns,
    require_hamilton_total,
    require_one_to_one_assignment,
    require_tier_split_consistency,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run upper-tier redesign + Hamilton + tier split.")
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("02_run_apportionment")

    cfg = load_config(args.config)
    log.info("Scenario: %s (%s)", cfg.scenario_name, cfg.scenario_id)

    # ------------------------------------------------------------------
    # 1. Load cleaned inputs.
    # ------------------------------------------------------------------
    parishes = gpd.read_file(cfg.path("clean_parishes_gpkg"), layer="parishes")
    require_columns(
        parishes,
        ["parish_id", "municipality_id", "distrito_ilha", "municipio", "geometry"],
        "parishes",
    )
    log.info("Loaded %d parishes", len(parishes))

    muni_voters = pd.read_csv(cfg.path("clean_municipality_voters_csv"), dtype={"municipality_id": str})
    require_columns(muni_voters, ["municipality_id", "registered_voters"], "municipality_voters")
    log.info("Loaded %d municipalities with voter totals", len(muni_voters))

    # Propagate municipality voter totals down to parishes by area share.
    # Why: the redesign module (specifically the contiguous-balanced-partition
    # for Lisboa/Porto) needs per-parish weights, but the authoritative voter
    # source is the municipality. We distribute each municipality's voters
    # over its parishes by area, which is the standard demographic
    # downscaling default and is conservative for the partition decision.
    parishes_voters = _allocate_voters_to_parishes(parishes, muni_voters)

    # ------------------------------------------------------------------
    # 2. Redesign upper tier.
    # ------------------------------------------------------------------
    log.info("Applying upper-tier redesign rules...")
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
    require_one_to_one_assignment(
        parishes_assigned, "parish_id", "upper_district", "upper_district assignment",
    )
    n_districts = parishes_assigned["upper_district"].nunique()
    log.info("Created %d redesigned upper-tier districts", n_districts)

    # ------------------------------------------------------------------
    # 3. Voter totals per upper district (using authoritative municipality data).
    # ------------------------------------------------------------------
    # Aggregate via municipality_id rather than the area-disaggregated
    # parish voters: this way the district totals exactly reproduce the
    # municipality-level totals from the official spreadsheet.
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
    log.info("Total registered voters across all districts: %d", district_voters["registered_voters"].sum())

    # ------------------------------------------------------------------
    # 4. Hamilton allocation.
    # ------------------------------------------------------------------
    hamilton = allocate_hamilton(district_voters, cfg.total_seats)
    require_hamilton_total(hamilton, cfg.total_seats)
    log.info("Hamilton allocated %d total mandates across %d districts",
             hamilton["total_mandates"].sum(), len(hamilton))

    # ------------------------------------------------------------------
    # 5. Tier split.
    # ------------------------------------------------------------------
    tier = split_tiers(
        hamilton,
        cfg.party_list_ratio,
        rounding=cfg.tier_split_rounding,
    )
    require_tier_split_consistency(tier)
    log.info("Tier split: %d party-list seats, %d single-member seats (rule=%s)",
             tier["party_list_seats"].sum(), tier["single_member_seats"].sum(),
             cfg.tier_split_rounding)

    # ------------------------------------------------------------------
    # 6. Write outputs.
    # ------------------------------------------------------------------
    out_tables = ROOT / cfg.paths["outputs_dir"] / "tables"
    out_geojson = ROOT / cfg.paths["outputs_dir"] / "geojson"

    # upper_district_membership.csv: parish -> upper_district
    membership = parishes_assigned[
        ["parish_id", "municipality_id", "municipio", "distrito_ilha", "upper_district"]
    ].copy()
    write_csv(membership, out_tables / "upper_district_membership.csv")

    # hamilton_allocation.csv
    write_csv(hamilton, out_tables / "hamilton_allocation.csv")

    # tier_split.csv
    write_csv(tier, out_tables / "tier_split.csv")

    # upper_district_diagnostics.csv: voters, district magnitude, parish/muni counts
    diag = (
        parishes_assigned.groupby("upper_district")
        .agg(
            n_parishes=("parish_id", "nunique"),
            n_municipalities=("municipality_id", "nunique"),
            n_distritos_original=("distrito_ilha", "nunique"),
        )
        .reset_index()
        .merge(district_voters, on="upper_district")
        .merge(hamilton[["upper_district", "total_mandates"]], on="upper_district")
        .merge(tier[["upper_district", "party_list_seats", "single_member_seats"]], on="upper_district")
    )
    write_csv(diag, out_tables / "upper_district_diagnostics.csv")

    # upper_districts.geojson: dissolved district polygons + voter totals + mandates
    parishes_full = parishes_assigned.merge(
        district_voters, on="upper_district", how="left", suffixes=("_parish", "")
    )
    # Use municipality voters at district level by setting voters_col-source = district aggregation.
    dissolved = parishes_assigned.dissolve(by="upper_district").reset_index()
    dissolved = dissolved[["upper_district", "geometry"]]
    dissolved = (
        dissolved.merge(district_voters, on="upper_district")
        .merge(hamilton[["upper_district", "total_mandates"]], on="upper_district")
        .merge(tier[["upper_district", "party_list_seats", "single_member_seats"]], on="upper_district")
    )
    dissolved = gpd.GeoDataFrame(dissolved, crs=parishes_assigned.crs)
    write_geojson(dissolved, out_geojson / "upper_districts.geojson", cfg.export_crs)

    log.info("Apportionment pipeline complete.")
    return 0


def _allocate_voters_to_parishes(
    parishes: gpd.GeoDataFrame, muni_voters: pd.DataFrame,
) -> pd.DataFrame:
    """Distribute each municipality's voter total across its parishes
    in proportion to area. Returns a DataFrame with parish_id and an
    estimated registered_voters column.

    The estimate is only used for sub-district partitioning (Lisboa/Porto
    splitting), which decides which group of *municipalities* goes into
    which sub-district; the precise per-parish number does not affect
    the outcome at the municipality level.
    """
    p = parishes[["parish_id", "municipality_id", "geometry"]].copy()
    p["area"] = p.geometry.area
    muni_area = p.groupby("municipality_id")["area"].sum().rename("muni_area")
    p = p.merge(muni_area, on="municipality_id", how="left")
    p["area_share"] = p["area"] / p["muni_area"]

    p = p.merge(
        muni_voters[["municipality_id", "registered_voters"]],
        on="municipality_id", how="left",
    )
    p["registered_voters"] = (p["registered_voters"] * p["area_share"]).round().astype(int)
    return p[["parish_id", "municipality_id", "registered_voters"]]


if __name__ == "__main__":
    sys.exit(main())
