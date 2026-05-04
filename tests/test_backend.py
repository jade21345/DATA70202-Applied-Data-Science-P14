"""End-to-end backend tests using FastAPI's TestClient.

These tests assume the algorithm pipeline has been run at least once
(i.e. outputs/scenarios/baseline_2025/ exists). They verify that every
documented endpoint returns 200 with the expected schema.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Skip the entire module if FastAPI dependencies are not installed.
fastapi_available = True
try:
    from fastapi.testclient import TestClient
except ImportError:
    fastapi_available = False

if fastapi_available:
    from backend.main import app


pytestmark = pytest.mark.skipif(
    not fastapi_available,
    reason="FastAPI not installed; backend tests require pip install -r requirements.txt",
)


@pytest.fixture(scope="module")
def client():
    """Reusable TestClient. Module-scoped so all tests share one app instance."""
    if not (ROOT / "outputs" / "scenarios" / "baseline_2025").exists():
        pytest.skip("Pipeline outputs not present; run scripts/04_run_full_pipeline.py first")
    return TestClient(app)


# -- health & scenario list -----------------------------------------------

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["n_scenarios"] >= 1


def test_list_scenarios(client):
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    body = r.json()
    ids = [s["scenario_id"] for s in body["data"]]
    assert "baseline_2025" in ids


def test_unknown_scenario_returns_404(client):
    r = client.get("/api/scenarios/does_not_exist/config")
    assert r.status_code == 404


# -- scenario config ------------------------------------------------------

def test_scenario_config(client):
    r = client.get("/api/scenarios/baseline_2025/config")
    assert r.status_code == 200
    body = r.json()
    assert body["scenario_id"] == "baseline_2025"
    assert body["total_seats"] == 226
    assert body["election_year"] == 2025
    # Numeric fields are present and well-typed.
    assert isinstance(body["party_list_ratio"], float)
    assert body["tier_split_rounding"] in ("floor", "round", "ceil")


# -- parties --------------------------------------------------------------

def test_parties(client):
    r = client.get("/api/scenarios/baseline_2025/parties")
    assert r.status_code == 200
    body = r.json()
    parties = {p["party_id"]: p for p in body["data"]}
    assert "ps" in parties
    assert "psd_cds" in parties
    assert "ch" in parties
    # Every party has the required display fields.
    for p in body["data"]:
        assert p["party_id"]
        assert p["party_id_raw"]
        assert p["party_name"]
        assert p["abbreviation"]
        assert p["color"].startswith("#")


# -- final results --------------------------------------------------------

def test_final_results_sum_match_summary(client):
    summary = client.get("/api/scenarios/baseline_2025/config").json()
    final = client.get("/api/scenarios/baseline_2025/results/final").json()
    total_in_data = sum(p["total_seats"] for p in final["data"])
    assert total_in_data == final["allocated_seats"]
    assert final["allocated_seats"] == summary["allocated_seats"]
    # Every row uses slug-style party id.
    for p in final["data"]:
        assert "/" not in p["party_id"]
        assert "." not in p["party_id"]


# -- hamilton -------------------------------------------------------------

def test_hamilton_sums_to_total(client):
    r = client.get("/api/scenarios/baseline_2025/results/hamilton")
    assert r.status_code == 200
    body = r.json()
    assert sum(row["total_mandates"] for row in body["data"]) == body["total_seats"]
    # 19 redesigned districts in the baseline scenario.
    assert len(body["data"]) == 19


# -- tier-split -----------------------------------------------------------

def test_tier_split_consistency(client):
    r = client.get("/api/scenarios/baseline_2025/results/tier-split")
    body = r.json()
    for row in body["data"]:
        assert row["party_list_seats"] + row["single_member_seats"] == row["total_mandates"]
    assert sum(row["single_member_seats"] for row in body["data"]) == 74
    assert sum(row["party_list_seats"] for row in body["data"]) == 152


# -- dhondt ---------------------------------------------------------------

def test_dhondt_seat_total(client):
    r = client.get("/api/scenarios/baseline_2025/results/dhondt")
    body = r.json()
    # Sum across all rows = total party-list seats nationally.
    total = sum(row["allocated_seats"] for row in body["data"])
    assert total == 152


# -- single-member winners ------------------------------------------------

def test_single_member_winners_count(client):
    r = client.get("/api/scenarios/baseline_2025/results/single-member-winners")
    body = r.json()
    # 74 lower-tier districts, exactly one row each.
    assert len(body["data"]) == 74


# -- maps -----------------------------------------------------------------

def test_upper_districts_geojson(client):
    r = client.get("/api/scenarios/baseline_2025/maps/upper-districts")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 19
    # Required properties present.
    f0 = body["features"][0]
    assert "upper_district_id" in f0["properties"]
    assert "upper_district_name" in f0["properties"]
    assert "registered_voters" in f0["properties"]
    assert "total_mandates" in f0["properties"]


def test_lower_districts_geojson(client):
    r = client.get("/api/scenarios/baseline_2025/maps/lower-districts")
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 74
    # Verify winner_party_id slug formatting (or null).
    for f in body["features"]:
        wid = f["properties"].get("winner_party_id")
        if wid is not None:
            assert "/" not in wid
            assert "." not in wid


# -- diagnostics ----------------------------------------------------------

def test_diagnostics_runs(client):
    r = client.get("/api/scenarios/baseline_2025/diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("valid", "valid_with_warnings", "invalid")
    assert isinstance(body["checks"], list)
    assert len(body["checks"]) > 0
    for c in body["checks"]:
        assert c["check_name"]
        assert c["severity"] in ("info", "warning", "error")
        assert c["message"]
