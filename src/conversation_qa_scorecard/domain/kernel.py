"""Vertical-neutral domain kernel: pure-stdlib types the service reasons over.

Taxonomies are ``StrEnum``s from the commons (a member IS its wire value), citations carry
provenance, and the WORM audit record is stored already-redacted. Nothing here imports a web
framework or a cloud SDK (the commons packages it uses are themselves stdlib).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from hex_service_kit.enums import LenientStrEnum


def utcnow() -> datetime:
    """Timezone-aware UTC now (the single clock the domain uses)."""
    return datetime.now(UTC)


class Severity(LenientStrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Decision(LenientStrEnum):
    ALLOWED = "allowed"
    ESCALATED = "escalated"  # routed to a human (maker-checker, P-06)


@dataclass(frozen=True, slots=True)
class Citation:
    """Provenance attached to a generated claim (source + optional locator)."""

    source_id: str
    title: str
    snippet: str = ""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable, already-redacted record of one interaction (P-04 / rule R2)."""

    action: str
    actor: str
    decision: Decision
    severity: Severity
    redacted_summary: str
    citations: tuple[Citation, ...] = ()
    timestamp: datetime = field(default_factory=utcnow)
