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


# --------------------------------------------------------------------------- #
# Safety (guardrail): the A1 Guardrail Gateway concerns, vertical-neutral (rule R1)
# --------------------------------------------------------------------------- #
class Direction(LenientStrEnum):
    """Which leg of a generation call a guardrail screen covers."""

    INPUT = "input"
    OUTPUT = "output"


class GuardrailCategory(LenientStrEnum):
    """What kind of thing a guardrail finding names. A fork adds to this; it never removes."""

    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    SENSITIVE_DATA = "sensitive_data"
    MALICIOUS_URL = "malicious_url"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class GuardrailFinding:
    """One thing a guardrail screen noticed, never the whole verdict on its own."""

    category: GuardrailCategory
    confidence: str  # "low" | "medium" | "high"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GuardrailVerdict:
    """What a guardrail screen decided about one direction of one generation call.

    ``sanitized_text`` is the text to use going forward when ``allowed`` is True (it may equal
    the input unchanged); it is ``None`` when the call is blocked, because a blocked call has no
    safe text to substitute.
    """

    allowed: bool
    direction: Direction
    findings: tuple[GuardrailFinding, ...] = ()
    sanitized_text: str | None = None
    reason: str = ""


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
