"""
Manifest writer.
Records a timestamped JSON file for every run so you can:
  1. Set exact Grafana time ranges for pre-staged tabs
  2. Know which time window to search in Tempo for the hero trace
  3. Reproduce or re-run a specific phase if something looked wrong
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


class ManifestWriter:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict = {"phases": {}}

    def write_start(self, ts: datetime) -> None:
        self._data["run_start"] = ts.isoformat()
        self._flush()

    def write_end(self, ts: datetime) -> None:
        self._data["run_end"] = ts.isoformat()
        self._flush()

    def write_phase_start(self, phase: str, ts: datetime) -> None:
        self._data["phases"].setdefault(phase, {})["start"] = ts.isoformat()
        self._flush()

    def write_phase_end(
        self, phase: str, ts: datetime, error: Optional[str] = None
    ) -> None:
        self._data["phases"].setdefault(phase, {})["end"] = ts.isoformat()
        if error:
            self._data["phases"][phase]["error"] = error
        self._flush()

    def _flush(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2))
