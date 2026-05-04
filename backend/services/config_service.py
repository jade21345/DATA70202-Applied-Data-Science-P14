"""Config and party-metadata loader.

Loads config/parties.csv (the canonical party metadata table) and
exposes lookup helpers for joining party metadata into responses. The
CSV maps the raw party_id (as appearing in source data, e.g.
'PPD/PSD.CDS-PP') to a slug-style id (e.g. 'psd_cds') plus display
fields.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class ConfigService:
    """Reads config/ files."""

    def __init__(self, project_root: Path):
        self.root = Path(project_root)
        self._parties_cache: pd.DataFrame | None = None

    def parties(self) -> pd.DataFrame:
        """Return the parties metadata DataFrame, caching after first read."""
        if self._parties_cache is None:
            path = self.root / "config" / "parties.csv"
            if not path.exists():
                raise FileNotFoundError(f"parties.csv not found at {path}")
            self._parties_cache = pd.read_csv(path)
            required = {"party_id", "party_id_short", "display_name", "short_name", "color"}
            missing = required - set(self._parties_cache.columns)
            if missing:
                raise ValueError(f"parties.csv missing columns: {missing}")
        return self._parties_cache

    def party_lookup_by_raw(self) -> dict[str, dict]:
        """Map raw party_id -> {party_id_short, display_name, short_name, color}."""
        df = self.parties()
        out = {}
        for _, row in df.iterrows():
            out[row["party_id"]] = {
                "party_id_short": row["party_id_short"],
                "display_name": row["display_name"],
                "short_name": row["short_name"],
                "color": row["color"],
                "notes": row.get("notes", "") or "",
            }
        return out


@lru_cache(maxsize=1)
def get_service() -> ConfigService:
    here = Path(__file__).resolve()
    project_root = here.parent.parent.parent
    return ConfigService(project_root)
