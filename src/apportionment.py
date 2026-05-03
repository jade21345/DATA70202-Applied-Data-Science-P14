"""Seat allocation algorithms.

Three independent, pure-function algorithms used by the pipeline:

1. ``allocate_hamilton`` distributes a fixed total of mandates among
   territorial districts in proportion to their registered voters
   (largest-remainder method).
2. ``split_tiers`` divides each district's mandate count into upper-tier
   (party-list) and lower-tier (single-member) seats according to the
   configured ratio.
3. ``allocate_dhondt`` distributes a district's party-list seats among
   parties in proportion to their votes (highest-averages method).

These functions take and return plain DataFrames so they are easy to
unit-test against the client's RESULTS_2025.xlsx ground truth.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def allocate_hamilton(
    voters_by_district: pd.DataFrame,
    total_seats: int,
    district_col: str = "upper_district",
    voters_col: str = "registered_voters",
) -> pd.DataFrame:
    """Allocate ``total_seats`` to districts using the Hamilton (largest
    remainder) method.

    Algorithm:
      1. Compute each district's quota = (voters_i / sum(voters)) * total_seats.
      2. Each district receives floor(quota) seats initially.
      3. Remaining seats are distributed one-by-one to districts with the
         largest decimal remainders (ties broken by district name to make
         the result deterministic).

    Parameters
    ----------
    voters_by_district : DataFrame
        Must contain ``district_col`` (unique key) and ``voters_col``.
    total_seats : int
        Total seats to allocate (e.g. 226 for the AR).
    district_col, voters_col : str
        Column names; defaults match this project's canonical schema.

    Returns
    -------
    DataFrame with one row per district and columns:
        district_col, registered_voters, voter_share, quota,
        floor_seats, remainder, extra_seat, total_mandates
    """
    if voters_by_district[district_col].duplicated().any():
        raise ValueError(f"Duplicate values in '{district_col}'.")
    if (voters_by_district[voters_col] < 0).any():
        raise ValueError(f"Negative values in '{voters_col}'.")
    if total_seats <= 0:
        raise ValueError("total_seats must be positive.")

    df = voters_by_district[[district_col, voters_col]].copy()
    df = df.rename(columns={voters_col: "registered_voters"})

    total_voters = df["registered_voters"].sum()
    if total_voters == 0:
        raise ValueError("Total registered voters is zero.")

    df["voter_share"] = df["registered_voters"] / total_voters
    df["quota"] = df["voter_share"] * total_seats
    df["floor_seats"] = np.floor(df["quota"]).astype(int)
    df["remainder"] = df["quota"] - df["floor_seats"]

    seats_so_far = int(df["floor_seats"].sum())
    extras_to_distribute = total_seats - seats_so_far
    if extras_to_distribute < 0:
        # Only possible if all quotas were already integers and floor sums > total;
        # a rounding artefact essentially impossible with real data.
        raise RuntimeError(
            f"Hamilton method overshot: floor seats sum to {seats_so_far} > {total_seats}"
        )

    # Rank by remainder (descending). Tie-break: alphabetical district name
    # (deterministic and reproducible across runs).
    df = df.sort_values(
        by=["remainder", district_col],
        ascending=[False, True],
        kind="stable",
    ).reset_index(drop=True)

    df["extra_seat"] = 0
    df.loc[: extras_to_distribute - 1, "extra_seat"] = 1
    df["total_mandates"] = df["floor_seats"] + df["extra_seat"]

    # Verification - should be exact.
    if df["total_mandates"].sum() != total_seats:
        raise RuntimeError(
            f"Hamilton allocation sums to {df['total_mandates'].sum()} != {total_seats}"
        )

    # Restore alphabetical ordering for output stability.
    df = df.sort_values(district_col, kind="stable").reset_index(drop=True)
    return df


def split_tiers(
    hamilton_alloc: pd.DataFrame,
    party_list_ratio: float,
    rounding: str = "floor",
    district_col: str = "upper_district",
    mandates_col: str = "total_mandates",
) -> pd.DataFrame:
    """Split each district's total mandates into party-list and single-member seats.

    Per Methodology section 4, with a configurable rounding rule:

        floor:  P_i = floor(party_list_ratio * S_i)   -> matches client RESULTS_2025
                                                         (mainland 70 + islands 4 = 74 SM in 2025)
        round:  P_i = round-half-away(party_list_ratio * S_i)
        ceil:   P_i = ceil(party_list_ratio * S_i)

    U_i = S_i - P_i in all three cases.

    The default 'floor' is chosen because it preserves the principle that
    multi-member seats never exceed the configured ratio (70 percent),
    and because it reproduces the client's spreadsheet exactly. The other
    options exist for sensitivity analysis.

    Returns a DataFrame with one row per district and columns:
        district_col, total_mandates, party_list_seats, single_member_seats
    """
    if not (0.0 < party_list_ratio < 1.0):
        raise ValueError(f"party_list_ratio must be in (0,1), got {party_list_ratio}")
    if rounding not in {"floor", "round", "ceil"}:
        raise ValueError(f"rounding must be 'floor', 'round', or 'ceil', got '{rounding}'")

    df = hamilton_alloc[[district_col, mandates_col]].copy()
    df = df.rename(columns={mandates_col: "total_mandates"})

    raw = party_list_ratio * df["total_mandates"]
    if rounding == "floor":
        df["party_list_seats"] = np.floor(raw).astype(int)
    elif rounding == "ceil":
        df["party_list_seats"] = np.ceil(raw).astype(int)
    else:  # 'round' = half-away-from-zero (matches Excel ROUND for non-negative)
        df["party_list_seats"] = np.floor(raw + 0.5).astype(int)

    df["single_member_seats"] = df["total_mandates"] - df["party_list_seats"]

    # Sanity checks.
    if (df["single_member_seats"] < 0).any():
        bad = df[df["single_member_seats"] < 0]
        raise RuntimeError(f"Negative single_member_seats produced:\n{bad}")
    bad = df[df["party_list_seats"] + df["single_member_seats"] != df["total_mandates"]]
    if len(bad):
        raise RuntimeError(f"P_i + U_i != S_i for rows:\n{bad}")

    return df.reset_index(drop=True)


def allocate_dhondt(
    party_votes: dict[str, int] | pd.Series,
    seats: int,
) -> dict[str, int]:
    """Distribute ``seats`` among parties using the D'Hondt highest-averages method.

    For each iteration, give the next seat to the party with the highest
    quotient ``votes_p / (seats_already_p + 1)``. Ties are broken by the
    party with more raw votes; if still tied, by alphabetical party
    name (deterministic).

    Parameters
    ----------
    party_votes : dict or Series
        Mapping party_id -> votes. Parties with zero votes are allowed
        (they will receive zero seats).
    seats : int
        Number of seats to allocate. May be 0 (returns all zeros).

    Returns
    -------
    dict
        Mapping party_id -> seats_won (sums to ``seats``).
    """
    if seats < 0:
        raise ValueError("seats must be non-negative.")

    if isinstance(party_votes, pd.Series):
        votes = party_votes.to_dict()
    else:
        votes = dict(party_votes)

    if any(v < 0 for v in votes.values()):
        raise ValueError("party_votes contains negative entries.")

    parties = sorted(votes.keys())  # alphabetical for deterministic tie-break
    allocated = {p: 0 for p in parties}

    if seats == 0 or all(v == 0 for v in votes.values()):
        return allocated

    for _ in range(seats):
        best_party = None
        best_quotient = -1.0
        best_votes = -1
        for p in parties:
            q = votes[p] / (allocated[p] + 1)
            # Tie-break order: higher quotient, then higher raw votes, then
            # alphabetical (already enforced by iteration order).
            if (q > best_quotient) or (
                q == best_quotient and votes[p] > best_votes
            ):
                best_quotient = q
                best_votes = votes[p]
                best_party = p
        allocated[best_party] += 1

    return allocated


def allocate_dhondt_by_district(
    party_votes_long: pd.DataFrame,
    seats_by_district: pd.DataFrame,
    district_col: str = "upper_district",
    party_col: str = "party",
    votes_col: str = "votes",
    seats_col: str = "party_list_seats",
) -> pd.DataFrame:
    """Run D'Hondt independently within each district.

    Parameters
    ----------
    party_votes_long : DataFrame
        Long-format table with one row per (district, party) and a votes column.
    seats_by_district : DataFrame
        Table with one row per district and a column giving the number of
        party-list seats to allocate in that district.

    Returns
    -------
    DataFrame
        Long-format with columns: district_col, party_col, votes_col,
        allocated_seats. Includes parties that won zero seats so the
        client downstream can render full grids.
    """
    seats_lookup = dict(zip(seats_by_district[district_col], seats_by_district[seats_col]))

    out_rows = []
    for district, group in party_votes_long.groupby(district_col):
        n_seats = int(seats_lookup.get(district, 0))
        votes_dict = dict(zip(group[party_col], group[votes_col].astype(int)))
        result = allocate_dhondt(votes_dict, n_seats)
        for party, seats_won in result.items():
            out_rows.append({
                district_col: district,
                party_col: party,
                votes_col: int(votes_dict[party]),
                "allocated_seats": int(seats_won),
            })

    out = pd.DataFrame(out_rows)
    # Stable ordering: by district, then seats desc, then votes desc.
    out = out.sort_values(
        by=[district_col, "allocated_seats", votes_col],
        ascending=[True, False, False],
        kind="stable",
    ).reset_index(drop=True)
    return out
