"""WarehouseExportPort: the analytics seam, and the narrowest data the service ever emits.

A conversation-QA programme is only useful when the whole estate can be looked at: adherence by
team, by market, by month. That is a warehouse question, so this port exists.

It takes :class:`ScorecardRow`, the deliberately FLAT projection of a scorecard, and never a
transcript, never an utterance and never an evidence span's text. Shipping utterances into an
analytics table is how a QA programme becomes a data-protection incident: the rows are joined,
copied into notebooks and exported to spreadsheets by people who never saw the retention
policy. The evidence stays in the tenant-scoped scorecard store, behind the 403.

The managed adapter streams to BigQuery in the residency region with a lazy import; the offline
adapter appends JSON Lines to a local path so the export is inspectable in the gate and the
demo; the on-premises adapter refuses, because the client's warehouse is the client's.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..domain.models import ScorecardRow


@runtime_checkable
class WarehouseExportPort(Protocol):
    def export(self, rows: Sequence[ScorecardRow]) -> int:
        """Append ``rows`` to the warehouse and return how many were accepted.

        Returning a count rather than ``None`` so a caller can record what left the service;
        an export that silently accepted nothing is indistinguishable from one that worked.
        """
        ...
