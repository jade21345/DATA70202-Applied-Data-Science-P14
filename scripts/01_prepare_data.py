"""ETL: load all four CAOP2025 geopackages and the official AR2025
results spreadsheet, normalise schemas, and emit clean files under
data_clean/ for downstream pipeline modules to consume.

Run from the project root:
    python scripts/01_prepare_data.py

Outputs:
    data_clean/parishes.gpkg          - all parishes nationwide, EPSG:3763
    data_clean/votes_2025.csv         - long-format party votes per parish
    data_clean/voters_2025.csv        - registered voters per parish
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow running this script directly without installing the package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import load_config  # noqa: E402
from io_utils import (  # noqa: E402
    filter_to_parish_rows,
    load_freguesias_unified,
    load_municipality_voters,
    load_official_results,
    voters_by_parish,
    write_csv,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare clean parish, vote, and voter data.")
    parser.add_argument(
        "--config", type=str, default=None,
        help="Path to scenario_config.json (default: config/scenario_config.json)",
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("01_prepare_data")

    cfg = load_config(args.config)
    log.info("Scenario: %s (%s)", cfg.scenario_name, cfg.scenario_id)

    # 1. Parishes (geometry only) -----------------------------------------
    parishes = load_freguesias_unified(
        continente_path=cfg.path("raw_continente_gpkg"),
        madeira_path=cfg.path("raw_madeira_gpkg"),
        acores_co_path=cfg.path("raw_acores_co_gpkg"),
        acores_w_path=cfg.path("raw_acores_w_gpkg"),
        target_crs=cfg.internal_crs,
    )
    parishes["parish_id"] = parishes["dtmnfr"]
    # Add a stable municipality_id column = first 4 digits of DICOFRE.
    parishes["municipality_id"] = parishes["parish_id"].str[:4]

    out_parishes = cfg.path("clean_parishes_gpkg")
    out_parishes.parent.mkdir(parents=True, exist_ok=True)
    parishes.to_file(out_parishes, driver="GPKG", layer="parishes")
    log.info("Wrote clean parishes: %s (%d rows)", out_parishes, len(parishes))

    # 2. Municipality-level registered voters (authoritative source) -------
    # We use the AR_2025_Concelho sheet rather than aggregating parish rows
    # because parish-level codes drift between elections (freguesia mergers
    # since 2013), causing ~10% of voters to fall on orphan codes that no
    # longer exist in CAOP. Municipality (DTMN) codes are stable.
    muni_voters = load_municipality_voters(
        xlsx_path=cfg.path("raw_results_xlsx"),
        sheet=f"AR_{cfg.election_year}_Concelho",
    )

    # Cross-check coverage.
    geo_munis = set(parishes["municipality_id"])
    vote_munis = set(muni_voters["municipality_id"])
    missing_in_votes = geo_munis - vote_munis
    missing_in_geo = vote_munis - geo_munis
    if missing_in_votes:
        log.warning(
            "%d municipalities in CAOP without voter data: %s",
            len(missing_in_votes), sorted(missing_in_votes),
        )
    if missing_in_geo:
        log.warning(
            "%d municipalities in vote table without geometry: %s",
            len(missing_in_geo), sorted(missing_in_geo),
        )
    if not missing_in_votes and not missing_in_geo:
        log.info("All %d municipalities matched between CAOP and vote table.", len(geo_munis))

    write_csv(muni_voters, cfg.path("clean_municipality_voters_csv"))

    # 3. Parish-level vote data (long format) -----------------------------
    # Used downstream for vote aggregation by upper/lower-tier district.
    # We tolerate the parish-level mismatch with CAOP because vote
    # aggregation rolls up to municipality before mapping to districts.
    votes_long = load_official_results(
        xlsx_path=cfg.path("raw_results_xlsx"),
        sheet=f"AR_{cfg.election_year}_Freguesia",
    )
    votes_long = filter_to_parish_rows(votes_long)
    votes_long = votes_long.rename(columns={
        "codigo": "parish_id",
        "nome_territorio": "parish_name",
    })
    # Add municipality_id for downstream aggregation.
    votes_long["municipality_id"] = votes_long["parish_id"].str[:4]

    # Diagnostic: how many parish-level vote rows have no matching CAOP geometry?
    geo_parishes = set(parishes["parish_id"])
    vote_parishes = set(votes_long["parish_id"])
    orphan_parishes = vote_parishes - geo_parishes
    if orphan_parishes:
        # Sum voters at the orphan parishes and check if the parent municipality is in CAOP.
        orphan_munis = {p[:4] for p in orphan_parishes}
        orphan_munis_in_geo = orphan_munis & geo_munis
        log.info(
            "%d parish vote rows have no direct CAOP geometry, but their %d "
            "parent municipalities are %d/%d covered. Vote aggregation will "
            "use municipality_id as the fallback join key.",
            len(orphan_parishes), len(orphan_munis),
            len(orphan_munis_in_geo), len(orphan_munis),
        )

    votes_clean = votes_long[
        ["parish_id", "parish_name", "municipality_id", "party", "votes"]
    ]
    write_csv(votes_clean, cfg.path("clean_election_csv"))

    # 4. Parish-level voters (best-effort, for lower-tier work) ------------
    voters_p = voters_by_parish(votes_long)
    voters_p["municipality_id"] = voters_p["parish_id"].str[:4]
    write_csv(voters_p, cfg.path("clean_voters_csv"))

    log.info("Done. Files written to %s", cfg.path("clean_voters_csv").parent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
