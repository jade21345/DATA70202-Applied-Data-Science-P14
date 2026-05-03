"""Unit tests for the apportionment algorithms.

These tests pin down the algorithms' behaviour against (a) hand-computed
small examples and (b) the client's RESULTS_2025.xlsx ground truth.

Run from project root:
    pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from apportionment import (  # noqa: E402
    allocate_dhondt,
    allocate_dhondt_by_district,
    allocate_hamilton,
    split_tiers,
)


# ---------------------------------------------------------------------------
# Hamilton
# ---------------------------------------------------------------------------

def test_hamilton_simple_case():
    """Trivial case: 3 districts with equal voters share 9 seats equally."""
    df = pd.DataFrame({"upper_district": ["A", "B", "C"], "registered_voters": [100, 100, 100]})
    out = allocate_hamilton(df, total_seats=9)
    assert out["total_mandates"].tolist() == [3, 3, 3]
    assert out["total_mandates"].sum() == 9


def test_hamilton_remainder_distribution():
    """Largest remainders get the extra seat."""
    # quotas: A=4.6, B=4.4, C=1.0; floor=4,4,1=9; one extra to A (rem 0.6 > 0.4 > 0.0).
    df = pd.DataFrame({
        "upper_district": ["A", "B", "C"],
        "registered_voters": [460, 440, 100],
    })
    out = allocate_hamilton(df, total_seats=10)
    out_sorted = out.set_index("upper_district")
    assert out_sorted.loc["A", "total_mandates"] == 5
    assert out_sorted.loc["B", "total_mandates"] == 4
    assert out_sorted.loc["C", "total_mandates"] == 1


def test_hamilton_total_always_equals_target():
    """Sum of allocated mandates must equal total_seats for any input."""
    import random
    rng = random.Random(42)
    for _ in range(20):
        n = rng.randint(2, 30)
        df = pd.DataFrame({
            "upper_district": [f"D{i}" for i in range(n)],
            "registered_voters": [rng.randint(1000, 1_000_000) for _ in range(n)],
        })
        total = rng.randint(n, 500)
        out = allocate_hamilton(df, total_seats=total)
        assert out["total_mandates"].sum() == total


def test_hamilton_rejects_invalid_input():
    df = pd.DataFrame({"upper_district": ["A", "A"], "registered_voters": [1, 1]})
    with pytest.raises(ValueError, match="Duplicate"):
        allocate_hamilton(df, 5)

    df = pd.DataFrame({"upper_district": ["A"], "registered_voters": [-1]})
    with pytest.raises(ValueError, match="Negative"):
        allocate_hamilton(df, 5)

    df = pd.DataFrame({"upper_district": ["A"], "registered_voters": [10]})
    with pytest.raises(ValueError, match="positive"):
        allocate_hamilton(df, 0)


# ---------------------------------------------------------------------------
# Tier split
# ---------------------------------------------------------------------------

def test_split_tiers_floor_matches_client():
    """The 'floor' rule must reproduce the client's spreadsheet exactly."""
    # Client expectations (S, P_floor) drawn from RESULTS_2025.xlsx.
    cases = [
        ("Aveiro", 16, 11), ("Braga", 19, 13), ("Coimbra", 9, 6), ("Faro", 9, 6),
        ("Leiria", 10, 7), ("Lisboa 1", 16, 11), ("Lisboa 3", 15, 10),
        ("Porto 2", 20, 14), ("Viseu", 8, 5), ("Beira Baixa", 7, 4),
    ]
    df = pd.DataFrame({
        "upper_district": [c[0] for c in cases],
        "total_mandates": [c[1] for c in cases],
    })
    out = split_tiers(df, party_list_ratio=0.70, rounding="floor")
    out = out.set_index("upper_district")
    for name, S, P in cases:
        assert out.loc[name, "party_list_seats"] == P, f"{name}: expected P={P}"
        assert out.loc[name, "single_member_seats"] == S - P


def test_split_tiers_seat_conservation():
    """P_i + U_i must equal S_i regardless of rounding rule."""
    df = pd.DataFrame({
        "upper_district": ["A", "B", "C"],
        "total_mandates": [10, 15, 8],
    })
    for rule in ("floor", "round", "ceil"):
        out = split_tiers(df, party_list_ratio=0.70, rounding=rule)
        assert (out["party_list_seats"] + out["single_member_seats"] == out["total_mandates"]).all()


def test_split_tiers_invalid_rule():
    df = pd.DataFrame({"upper_district": ["A"], "total_mandates": [10]})
    with pytest.raises(ValueError, match="rounding"):
        split_tiers(df, 0.70, rounding="banker")


# ---------------------------------------------------------------------------
# D'Hondt
# ---------------------------------------------------------------------------

def test_dhondt_canonical_example():
    """Wikipedia D'Hondt example: parties P,Q,R,S with 100k/80k/30k/20k votes,
    8 seats. Expected: P=4, Q=3, R=1, S=0."""
    votes = {"P": 100_000, "Q": 80_000, "R": 30_000, "S": 20_000}
    out = allocate_dhondt(votes, seats=8)
    assert out == {"P": 4, "Q": 3, "R": 1, "S": 0}


def test_dhondt_zero_seats_returns_zeros():
    out = allocate_dhondt({"A": 100, "B": 50}, seats=0)
    assert out == {"A": 0, "B": 0}


def test_dhondt_rejects_negative():
    with pytest.raises(ValueError):
        allocate_dhondt({"A": -1}, seats=1)
    with pytest.raises(ValueError):
        allocate_dhondt({"A": 100}, seats=-1)


def test_dhondt_seat_total_is_exact():
    """Sum of allocated seats must equal the requested number."""
    votes = {"P": 1_234_567, "Q": 765_432, "R": 543_210}
    for seats in [1, 5, 10, 50]:
        out = allocate_dhondt(votes, seats=seats)
        assert sum(out.values()) == seats


def test_dhondt_by_district_aggregate_consistency():
    """Sum across parties in each district should equal the district's seat total."""
    votes_long = pd.DataFrame([
        {"upper_district": "D1", "party": "P", "votes": 600},
        {"upper_district": "D1", "party": "Q", "votes": 400},
        {"upper_district": "D2", "party": "P", "votes": 100},
        {"upper_district": "D2", "party": "Q", "votes": 300},
    ])
    seats = pd.DataFrame([
        {"upper_district": "D1", "party_list_seats": 5},
        {"upper_district": "D2", "party_list_seats": 3},
    ])
    out = allocate_dhondt_by_district(votes_long, seats)
    by_district = out.groupby("upper_district")["allocated_seats"].sum()
    assert by_district["D1"] == 5
    assert by_district["D2"] == 3
