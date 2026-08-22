"""Domain error types: the refusals this vertical makes, each with a status a surface can map.

They live in the pure domain, not in an adapter, because the REFUSAL is a domain decision. A
tenant boundary enforced in one driving adapter is a tenant boundary the CLI and the agent
surface do not have, so the domain raises and every surface inherits the answer.
"""

from __future__ import annotations


class ScorePackError(ValueError):
    """The score pack is unreadable, incomplete or internally inconsistent.

    Raised at LOAD time, never at score time. A scorecard produced from a half-parsed pack
    would report an unconfigured obligation as a pass, which is the exact harm this service
    exists to prevent, so the pack loader refuses to start instead.
    """


class TenantAccessDeniedError(PermissionError):
    """A verified principal asked for a scorecard belonging to a different tenant.

    Deliberately 403 and not 404. The record EXISTS and the caller may not have it; answering
    404 would make the store's contents probeable by anyone with an id generator, and it would
    also tell an operator reading the logs the wrong story about what went wrong.
    """

    http_status = 403


class ScorecardNotFoundError(LookupError):
    """No scorecard with that id exists in this deployment's store."""

    http_status = 404


class NarrationRejectedError(ValueError):
    """A model draft failed the grounding schema and was discarded.

    Never surfaced to a caller as an error: the service falls back to the deterministic
    summary and records the rejection. It exists so the rejection is a typed, testable event
    rather than a silently swallowed branch.
    """
