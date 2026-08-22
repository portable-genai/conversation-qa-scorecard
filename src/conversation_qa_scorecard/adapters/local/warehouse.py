"""Local WarehouseExportPort: SDK-free JSON Lines export, inspectable in the gate and the demo.

The offline stand-in for BigQuery. Rows are held in memory and, when ``warehouse_path`` names
a file, appended to it as JSON Lines so a reviewer can open the export and see exactly what
left the service.

That inspectability is the point rather than a convenience. The claim this repo makes is that
the analytics feed carries the SHAPE of an answer and never an utterance, and a claim about
what leaves a service is only worth anything if somebody can read what left. The rows are
:class:`ScorecardRow`, which has no field that can hold transcript text.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from hex_service_kit.serialization import to_jsonable

from ...config import Settings
from ...domain.models import ScorecardRow


class LocalWarehouseExport:
    """Append flat scorecard rows to an in-memory list and, optionally, to a JSONL file."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        configured = (settings.warehouse_path or "").strip()
        self._path = Path(configured) if configured else None
        self._rows: list[ScorecardRow] = []

    @property
    def rows(self) -> tuple[ScorecardRow, ...]:
        """Everything exported in this process, for the tests, the eval and the demo."""
        return tuple(self._rows)

    @property
    def path(self) -> Path | None:
        return self._path

    def export(self, rows: Sequence[ScorecardRow]) -> int:
        accepted = list(rows)
        self._rows.extend(accepted)
        if self._path is not None and accepted:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                for row in accepted:
                    handle.write(json.dumps(to_jsonable(row), sort_keys=True) + "\n")
        return len(accepted)
