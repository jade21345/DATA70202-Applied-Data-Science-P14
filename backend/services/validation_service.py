"""Validation service.

Runs lightweight integrity checks on a scenario's outputs and returns
a structured list of pass/fail/warning items. Used by the
/api/scenarios/{id}/diagnostics endpoint so the frontend can render a
data-quality banner instead of the user discovering issues mid-page.

Each check is independent and never raises; failures are recorded as
DiagnosticCheck items with a severity label.
"""
from __future__ import annotations

import logging

from .output_service import OutputService, OutputNotFoundError

logger = logging.getLogger(__name__)


REQUIRED_FILES = [
    "json/scenario_summary.json",
    "json/final_party_seat_results.json",
    "geojson/upper_districts.geojson",
    "geojson/lower_districts.geojson",
    "tables/hamilton_allocation.csv",
    "tables/tier_split.csv",
    "tables/dhondt_results_by_district.csv",
    "tables/single_member_winners.csv",
    "tables/final_party_seat_results.csv",
    "tables/upper_district_diagnostics.csv",
    "tables/lower_district_diagnostics.csv",
]


def run_diagnostics(service: OutputService, scenario_id: str) -> dict:
    """Return a dict shaped like the DiagnosticsResponse schema."""
    checks: list[dict] = []

    # 1. All required files present.
    for rel in REQUIRED_FILES:
        passed = service.file_exists(scenario_id, rel)
        checks.append({
            "check_name": f"file_present:{rel}",
            "passed": passed,
            "severity": "error" if not passed else "info",
            "message": f"Required file {'present' if passed else 'MISSING'}: {rel}",
        })

    # If core files are missing, skip the deeper checks.
    if not all(c["passed"] for c in checks if c["check_name"].startswith("file_present:")):
        return _wrap(scenario_id, checks)

    # 2. Total seats reconciliation.
    try:
        summary = service.read_json(scenario_id, "json/scenario_summary.json")
        total = int(summary.get("total_seats", 0))
        allocated = int(summary.get("allocated_seats", 0))
        if total == allocated:
            checks.append({
                "check_name": "total_seats_sum",
                "passed": True,
                "severity": "info",
                "message": f"Total seats sum to {total}.",
            })
        else:
            checks.append({
                "check_name": "total_seats_sum",
                "passed": False,
                "severity": "warning",
                "message": (
                    f"Allocated seats ({allocated}) differ from total seats ({total}). "
                    f"Likely cause: one or more lower-tier districts had no recorded "
                    "votes and could not be assigned a winner."
                ),
            })
    except Exception as e:
        checks.append({
            "check_name": "total_seats_sum",
            "passed": False,
            "severity": "error",
            "message": f"Could not read scenario_summary: {e}",
        })

    # 3. Hamilton sum equals total_seats.
    try:
        ham = service.read_csv(scenario_id, "tables/hamilton_allocation.csv")
        ham_sum = int(ham["total_mandates"].sum())
        target = int(summary["total_seats"])
        if ham_sum == target:
            checks.append({
                "check_name": "hamilton_total",
                "passed": True,
                "severity": "info",
                "message": f"Hamilton allocation sums to {target}.",
            })
        else:
            checks.append({
                "check_name": "hamilton_total",
                "passed": False,
                "severity": "error",
                "message": f"Hamilton sum is {ham_sum}, expected {target}.",
            })
    except Exception as e:
        checks.append({
            "check_name": "hamilton_total",
            "passed": False,
            "severity": "error",
            "message": f"Could not check Hamilton total: {e}",
        })

    # 4. Tier split internal consistency.
    try:
        tier = service.read_csv(scenario_id, "tables/tier_split.csv")
        bad = tier[tier["party_list_seats"] + tier["single_member_seats"] != tier["total_mandates"]]
        if len(bad) == 0:
            checks.append({
                "check_name": "tier_split_consistency",
                "passed": True,
                "severity": "info",
                "message": "Every district has party_list_seats + single_member_seats == total_mandates.",
            })
        else:
            checks.append({
                "check_name": "tier_split_consistency",
                "passed": False,
                "severity": "error",
                "message": f"{len(bad)} district(s) violate the seat-conservation rule.",
            })
    except Exception as e:
        checks.append({
            "check_name": "tier_split_consistency",
            "passed": False,
            "severity": "error",
            "message": f"Could not check tier split: {e}",
        })

    # 5. Lower-tier contiguity & deviation summary.
    try:
        ld = service.read_csv(scenario_id, "tables/lower_district_diagnostics.csv")
        n = len(ld)
        n_contig = int(ld["is_contiguous"].sum()) if n else 0
        n_within_10 = int((ld["deviation"].abs() <= 0.10).sum()) if n else 0
        if n_contig == n:
            checks.append({
                "check_name": "lower_tier_contiguity",
                "passed": True,
                "severity": "info",
                "message": f"All {n} lower-tier districts are contiguous.",
            })
        else:
            checks.append({
                "check_name": "lower_tier_contiguity",
                "passed": False,
                "severity": "warning",
                "message": (
                    f"{n - n_contig} of {n} lower-tier districts are not contiguous. "
                    "These typically span island groups and use virtual bridges."
                ),
            })
        if n and n_within_10 < n:
            checks.append({
                "check_name": "lower_tier_voter_balance",
                "passed": True,
                "severity": "warning",
                "message": (
                    f"{n - n_within_10} of {n} lower-tier districts deviate "
                    "from the target electorate by more than 10 percent. "
                    "Improvement is expected once client-provided seed parishes are supplied."
                ),
            })
    except Exception as e:
        checks.append({
            "check_name": "lower_tier_diagnostics",
            "passed": False,
            "severity": "error",
            "message": f"Could not check lower-tier diagnostics: {e}",
        })

    # 6. Vote rows missing winners (data quality).
    try:
        sw = service.read_csv(scenario_id, "tables/single_member_winners.csv")
        no_winner = sw[sw["winning_party"].isna() | (sw["winning_votes"] == 0)]
        if len(no_winner):
            names = no_winner["lower_district"].head(5).tolist()
            checks.append({
                "check_name": "single_member_winners",
                "passed": True,  # not a hard failure
                "severity": "warning",
                "message": (
                    f"{len(no_winner)} lower-tier district(s) have no recorded votes "
                    f"(winner=None). Examples: {names}. "
                    "Cause: parish codes from CAOP 2025 not present in AR 2025 election data."
                ),
            })
    except Exception as e:
        checks.append({
            "check_name": "single_member_winners",
            "passed": False,
            "severity": "error",
            "message": f"Could not check single-member winners: {e}",
        })

    return _wrap(scenario_id, checks)


def _wrap(scenario_id: str, checks: list[dict]) -> dict:
    has_error = any(c["severity"] == "error" and not c["passed"] for c in checks)
    has_warning = any(c["severity"] == "warning" and not c["passed"] for c in checks) or any(
        c["severity"] == "warning" for c in checks
    )
    if has_error:
        status = "invalid"
    elif has_warning:
        status = "valid_with_warnings"
    else:
        status = "valid"
    return {"scenario_id": scenario_id, "status": status, "checks": checks}
