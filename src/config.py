"""Configuration loader for the Portugal mixed-member model pipeline.

All scenario parameters live in config/scenario_config.json. This module
parses the JSON into a typed Config object so that downstream code does not
have to do string lookups everywhere. The intent is that *all* tunable
behaviour (election year, total seats, merge groups, file paths, CRS) can
be changed from the JSON without touching Python code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Root directory of the project (the parent of src/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "scenario_config.json"


@dataclass(frozen=True)
class MergeGroup:
    """A group of original distritos that are merged into one redesigned district."""
    name: str
    members: tuple[str, ...]
    rationale: str = ""


@dataclass(frozen=True)
class SplitRule:
    """Rule for splitting a single large distrito into k contiguous sub-districts."""
    district: str
    method: str
    k: int
    balance_target: str
    random_state: int
    improve_iters: int


@dataclass(frozen=True)
class UpperTierRedesign:
    """All rules governing how original distritos are reshaped into the upper tier."""
    merge_strategy: str
    merge_groups: tuple[MergeGroup, ...]
    split_rules: tuple[SplitRule, ...]
    always_merged: tuple[MergeGroup, ...]


@dataclass(frozen=True)
class LowerTierConfig:
    """Parameters for the lower-tier (single-member) districting algorithm."""
    seed_strategy: str
    skip_districts: tuple[str, ...]
    tolerance: float
    max_iterations: int
    tie_break_rule: str
    allow_repair: bool


@dataclass(frozen=True)
class Config:
    """Top-level scenario configuration."""
    scenario_id: str
    scenario_name: str
    election_year: int
    data_version: str
    created_at: str

    total_seats: int
    party_list_ratio: float
    single_member_ratio: float
    deviation_tolerance: float
    tier_split_rounding: str

    district_apportionment_method: str
    party_list_allocation_method: str
    lower_tier_method: str

    exclude_overseas: bool

    upper_tier: UpperTierRedesign
    lower_tier: LowerTierConfig

    internal_crs: str
    export_crs: str

    paths: dict[str, Path]

    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    # --- convenience ----------------------------------------------------

    def path(self, key: str) -> Path:
        """Return an absolute Path for a configured path key.

        Relative paths in the JSON are resolved against PROJECT_ROOT.
        """
        p = self.paths[key]
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    def merge_group_lookup(self) -> dict[str, str]:
        """Build distrito_name -> redesigned_district_name map for ALL merge groups
        (both manual merge_groups and always_merged)."""
        lookup: dict[str, str] = {}
        for grp in self.upper_tier.merge_groups + self.upper_tier.always_merged:
            for member in grp.members:
                if member in lookup:
                    raise ValueError(
                        f"Distrito '{member}' appears in multiple merge groups: "
                        f"'{lookup[member]}' and '{grp.name}'"
                    )
                lookup[member] = grp.name
        return lookup

    def split_district_names(self) -> set[str]:
        """Names of original distritos that should be split into sub-districts."""
        return {rule.district for rule in self.upper_tier.split_rules}


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate the scenario configuration from JSON.

    Parameters
    ----------
    path : str | Path | None
        Path to the scenario config JSON. Defaults to
        config/scenario_config.json under the project root.

    Returns
    -------
    Config
        Parsed configuration object.
    """
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    upper_raw = raw["upper_tier_redesign"]

    upper = UpperTierRedesign(
        merge_strategy=upper_raw["merge_strategy"],
        merge_groups=tuple(
            MergeGroup(
                name=g["name"],
                members=tuple(g["members"]),
                rationale=g.get("rationale", ""),
            )
            for g in upper_raw.get("merge_groups", [])
        ),
        split_rules=tuple(
            SplitRule(
                district=r["district"],
                method=r["method"],
                k=int(r["k"]),
                balance_target=r["balance_target"],
                random_state=int(r.get("random_state", 0)),
                improve_iters=int(r.get("improve_iters", 10000)),
            )
            for r in upper_raw.get("split_rules", [])
        ),
        always_merged=tuple(
            MergeGroup(
                name=g["name"],
                members=tuple(g["members"]),
                rationale=g.get("rationale", ""),
            )
            for g in upper_raw.get("always_merged", [])
        ),
    )

    lower_raw = raw["lower_tier"]
    lower = LowerTierConfig(
        seed_strategy=lower_raw["seed_strategy"],
        skip_districts=tuple(lower_raw.get("skip_districts", [])),
        tolerance=float(lower_raw["tolerance"]),
        max_iterations=int(lower_raw["max_iterations"]),
        tie_break_rule=lower_raw["tie_break_rule"],
        allow_repair=bool(lower_raw["allow_repair"]),
    )

    paths = {k: Path(v) for k, v in raw["paths"].items()}

    cfg = Config(
        scenario_id=raw["scenario_id"],
        scenario_name=raw["scenario_name"],
        election_year=int(raw["election_year"]),
        data_version=raw["data_version"],
        created_at=raw["created_at"],
        total_seats=int(raw["total_seats"]),
        party_list_ratio=float(raw["party_list_ratio"]),
        single_member_ratio=float(raw["single_member_ratio"]),
        deviation_tolerance=float(raw["deviation_tolerance"]),
        tier_split_rounding=raw.get("tier_split_rounding", "floor"),
        district_apportionment_method=raw["district_apportionment_method"],
        party_list_allocation_method=raw["party_list_allocation_method"],
        lower_tier_method=raw["lower_tier_method"],
        exclude_overseas=bool(raw["exclude_overseas"]),
        upper_tier=upper,
        lower_tier=lower,
        internal_crs=raw["crs"]["internal"],
        export_crs=raw["crs"]["export"],
        paths=paths,
        raw=raw,
    )

    _validate_config(cfg)
    return cfg


def _validate_config(cfg: Config) -> None:
    """Sanity checks on configuration consistency."""
    if cfg.upper_tier.merge_strategy != "manual":
        raise NotImplementedError(
            f"merge_strategy='{cfg.upper_tier.merge_strategy}' is reserved but "
            "not implemented. Use 'manual' for now."
        )
    if cfg.tier_split_rounding not in {"floor", "round", "ceil"}:
        raise ValueError(
            f"tier_split_rounding must be 'floor', 'round', or 'ceil', "
            f"got '{cfg.tier_split_rounding}'"
        )
    # ratios must sum to 1
    ratio_sum = cfg.party_list_ratio + cfg.single_member_ratio
    if abs(ratio_sum - 1.0) > 1e-6:
        raise ValueError(
            f"party_list_ratio + single_member_ratio must equal 1.0, got {ratio_sum}"
        )
    # detect a member appearing in both merge_groups and always_merged
    cfg.merge_group_lookup()  # raises on duplicates
    # split rules must not target a name that is also a merge member
    merge_members = {
        m for grp in cfg.upper_tier.merge_groups + cfg.upper_tier.always_merged
        for m in grp.members
    }
    for rule in cfg.upper_tier.split_rules:
        if rule.district in merge_members:
            raise ValueError(
                f"Distrito '{rule.district}' is configured to be split AND "
                f"merged. These are mutually exclusive."
            )
