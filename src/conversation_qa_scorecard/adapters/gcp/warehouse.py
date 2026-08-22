"""Managed WarehouseExportPort: BigQuery in the residency region (SDK imports stay lazy).

Streams the FLAT :class:`ScorecardRow` projection and nothing else. There is no field on that
row that can hold an utterance, which is the control rather than a convention: an analytics
table is joined, copied into notebooks and exported to spreadsheets by people who never saw the
retention policy, so the evidence stays in the tenant-scoped scorecard store behind the 403.

An unconfigured destination REFUSES. An export that silently goes nowhere is worse than one
that fails, because the dashboard it feeds keeps showing yesterday's numbers and nobody knows.
"""

from __future__ import annotations

from collections.abc import Sequence

from hex_service_kit.serialization import to_jsonable

from ...config import Settings
from ...domain.models import ScorecardRow


class BigQueryWarehouseExport:
    """Stream flat scorecard rows into the deployment's warehouse table."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def export(self, rows: Sequence[ScorecardRow]) -> int:
        table = (self._settings.warehouse_table or "").strip()
        if not table:
            raise RuntimeError(
                "warehouse_table is not configured, so the managed export has no destination. "
                "Set CONVQA_WAREHOUSE_TABLE to project.dataset.table; refusing rather than "
                "accepting rows that would go nowhere."
            )
        if not rows:
            return 0
        # Lazy import: absent in the offline profiles and in CI.
        from google.cloud import bigquery  # pragma: no cover - needs live GCP

        client = bigquery.Client()  # pragma: no cover - needs live GCP
        errors = client.insert_rows_json(  # pragma: no cover - needs live GCP
            table, [to_jsonable(row) for row in rows]
        )
        if errors:  # pragma: no cover - needs live GCP
            raise RuntimeError(f"warehouse export rejected {len(errors)} rows: {errors[:3]}")
        return len(rows)  # pragma: no cover - needs live GCP
