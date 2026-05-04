"""End-to-end validation: compare our pipeline outputs against the
client's RESULTS_2025.xlsx ground truth.

Reports any district where total_mandates or single_member_seats differ.
This is the canonical regression test for the apportionment side of the
pipeline. If it passes today, any future change to apportionment.py
should also pass it (or have a documented reason for the difference).

Run from the project root:
    python scripts/03_validate_against_client.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from config import load_config  # noqa: E402


# Mapping from the client's verbose district names (with parenthesised
# composition) to the canonical names used throughout our pipeline.
CLIENT_NAME_TO_CANON = {
    "Trás-os-Montes (Bragança + Vila Real)": "Trás-os-Montes",
    "Alentejo (Portalegre + Évora + Beja)": "Alentejo",
    "Beira Baixa (Castelo Branco + Guarda)": "Beira Baixa",
}


def main() -> int:
    cfg = load_config()

    client_xlsx = ROOT / "data_raw" / "RESULTS_2025.xlsx"
    if not client_xlsx.exists():
        print(f"ERROR: client reference file not found: {client_xlsx}")
        return 2

    # Load client ground truth from Single-member sheet.
    client = pd.read_excel(client_xlsx, sheet_name="Single-member", header=3).iloc[:19].copy()
    client = client.rename(columns={
        "Unnamed: 1": "client_district",
        "registered voters": "client_voters",
        "Total of mandates": "client_mandates",
        "Single-member districts with column O": "client_single_member",
    })[["client_district", "client_voters", "client_mandates", "client_single_member"]]

    client["upper_district"] = client["client_district"].map(
        lambda x: CLIENT_NAME_TO_CANON.get(x, x)
    )
    for c in ("client_voters", "client_mandates", "client_single_member"):
        client[c] = client[c].astype(int)

    # Load our outputs.
    out_tables = cfg.scenario_outputs_dir() / "tables"
    ham = pd.read_csv(out_tables / "hamilton_allocation.csv")
    tier = pd.read_csv(out_tables / "tier_split.csv")
    diag = pd.read_csv(out_tables / "upper_district_diagnostics.csv")

    # Merge.
    ours = ham.merge(tier, on=["upper_district", "total_mandates"])
    merged = client.merge(ours, on="upper_district", how="outer", indicator=True)

    only_in_client = merged[merged["_merge"] == "left_only"]
    only_in_ours = merged[merged["_merge"] == "right_only"]
    if len(only_in_client):
        print(f"FAIL: {len(only_in_client)} client districts not produced by pipeline:")
        print(only_in_client[["upper_district"]].to_string(index=False))
        return 1
    if len(only_in_ours):
        print(f"FAIL: {len(only_in_ours)} pipeline districts not in client output:")
        print(only_in_ours[["upper_district"]].to_string(index=False))
        return 1

    matched = merged[merged["_merge"] == "both"].copy()
    matched["voters_diff"] = matched["registered_voters"] - matched["client_voters"]
    matched["mandates_diff"] = matched["total_mandates"] - matched["client_mandates"]
    matched["sm_diff"] = matched["single_member_seats"] - matched["client_single_member"]

    print("=" * 100)
    print(f"Validation against client RESULTS_2025.xlsx ({cfg.election_year})")
    print(f"Tier split rule in pipeline: {cfg.tier_split_rounding}")
    print("=" * 100)

    cols = ["upper_district", "client_voters", "registered_voters", "voters_diff",
            "client_mandates", "total_mandates", "mandates_diff",
            "client_single_member", "single_member_seats", "sm_diff"]
    print(matched[cols].to_string(index=False))
    print()

    n_voters_off = (matched["voters_diff"] != 0).sum()
    n_mandates_off = (matched["mandates_diff"] != 0).sum()
    n_sm_off = (matched["sm_diff"] != 0).sum()

    print("-" * 100)
    print(f"Voters mismatches:     {n_voters_off}/19")
    print(f"Mandates mismatches:   {n_mandates_off}/19")
    print(f"Single-member mismatches: {n_sm_off}/19")
    print()
    total_ours = matched["total_mandates"].sum()
    total_client = matched["client_mandates"].sum()
    print(f"Total mandates: pipeline={total_ours}, client={total_client}")
    sm_ours = matched["single_member_seats"].sum()
    sm_client = matched["client_single_member"].sum()
    print(f"Total single-member: pipeline={sm_ours}, client={sm_client}")

    # Mainland vs island breakdown
    islands = ["Madeira", "Açores"]
    mainland = matched[~matched["upper_district"].isin(islands)]
    isl = matched[matched["upper_district"].isin(islands)]
    print(f"  Mainland single-member: pipeline={mainland['single_member_seats'].sum()}, "
          f"client={mainland['client_single_member'].sum()}")
    print(f"  Islands single-member:  pipeline={isl['single_member_seats'].sum()}, "
          f"client={isl['client_single_member'].sum()}")

    return 0 if (n_voters_off == 0 and n_mandates_off == 0 and n_sm_off == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
