"""On-prem WarehouseExportPort: fail-fast portability placeholder (the exit proof, P-12)."""

from __future__ import annotations

from collections.abc import Sequence

from ...config import Settings
from ...domain.models import ScorecardRow


class OnPremWarehouseExport:
    """Satisfies WarehouseExportPort but refuses: the client's warehouse is the client's."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def export(self, rows: Sequence[ScorecardRow]) -> int:
        raise NotImplementedError(
            "on-prem warehouse export is a portability placeholder: bind the client's own "
            "warehouse loader (see docs/onprem-migration.md). Reporting zero rows accepted "
            "would look exactly like a working export with nothing to send."
        )
