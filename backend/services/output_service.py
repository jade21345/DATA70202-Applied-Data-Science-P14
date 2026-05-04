"""Output file loader.

Centralises file-system access for the backend. Every endpoint goes
through this service to read a CSV / GeoJSON / JSON from
outputs/scenarios/<id>/. Caching is light: results are cached in
memory by (scenario_id, file_path) since output files are immutable
between pipeline runs.

If a file is missing or malformed, ``OutputNotFoundError`` /
``OutputInvalidError`` are raised; the API layer translates these into
HTTP 404 / 500 responses.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class OutputNotFoundError(Exception):
    """Raised when a requested output file does not exist."""


class OutputInvalidError(Exception):
    """Raised when a file exists but cannot be parsed."""


class OutputService:
    """Reads outputs/scenarios/<scenario_id>/ files for the API."""

    def __init__(self, project_root: Path):
        self.root = Path(project_root)
        self.scenarios_dir = self.root / "outputs" / "scenarios"

    def list_scenarios(self) -> list[str]:
        """Return the list of scenario ids that have an outputs subdirectory."""
        if not self.scenarios_dir.is_dir():
            return []
        return sorted(p.name for p in self.scenarios_dir.iterdir() if p.is_dir())

    def scenario_path(self, scenario_id: str) -> Path:
        return self.scenarios_dir / scenario_id

    def _resolve(self, scenario_id: str, relative: str) -> Path:
        path = self.scenario_path(scenario_id) / relative
        if not path.exists():
            raise OutputNotFoundError(
                f"Output file not found: {relative} (scenario: {scenario_id})"
            )
        return path

    def read_csv(self, scenario_id: str, relative: str, dtype: dict | None = None) -> pd.DataFrame:
        path = self._resolve(scenario_id, relative)
        try:
            return pd.read_csv(path, dtype=dtype)
        except Exception as e:
            raise OutputInvalidError(f"Failed to parse CSV {relative}: {e}") from e

    def read_json(self, scenario_id: str, relative: str) -> Any:
        path = self._resolve(scenario_id, relative)
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            raise OutputInvalidError(f"Failed to parse JSON {relative}: {e}") from e

    def read_geojson(self, scenario_id: str, relative: str) -> dict:
        # GeoJSON is just JSON.
        return self.read_json(scenario_id, relative)

    def file_exists(self, scenario_id: str, relative: str) -> bool:
        return (self.scenario_path(scenario_id) / relative).exists()


# Module-level singleton helper. The backend imports `get_service()` so
# tests can swap in a fake. lru_cache(1) gives us a single instance
# across the process.
@lru_cache(maxsize=1)
def get_service() -> OutputService:
    # Project root is the parent of `backend/`.
    here = Path(__file__).resolve()
    project_root = here.parent.parent.parent
    return OutputService(project_root)
