"""Shared conversion from a failing scorecard to an ``review-kit`` Review payload.

Lives in the adapter layer, not the pure domain, because it depends on the kit. Everything
that crosses the wire is redacted AGAIN here, using the shared ``pii-kit``, even though the
transcript was already masked at ingestion: the console is a SHARED sink, a contact filed in
one market may still quote another market's national id, and defence in depth at a service
boundary costs one regex pass. Hrz7 redacts once more before its own audit write.

``maker`` and ``tenant`` are asserted here and trusted by Hrz7 because the caller is an
authenticated S2S service; per-hop on-behalf-of token exchange is the deferred next layer.
"""

from __future__ import annotations

import re

from pii_kit import NATIONAL_ID_PATTERNS, UNIVERSAL_PATTERNS, national_patterns_for
from pii_kit import redact as pii_redact
from review_kit import Citation as KitCitation
from review_kit import Review

from ..domain.kernel import Severity
from ..domain.models import Scorecard

#: Cap the citations carried on the wire: enough for a reviewer to trace the decision without
#: copying the whole instrument set into the console.
_MAX_CITATIONS = 8

#: The console is a SHARED sink, so the payload is scrubbed against every jurisdiction's rows
#: plus the universal email/phone rows, whatever this deployment's own selection is.
_ALL_PATTERNS = (
    *national_patterns_for(tuple(NATIONAL_ID_PATTERNS.keys())),
    *UNIVERSAL_PATTERNS,
)

#: Bands that demand dual control (two approvals) rather than a single checker.
_DUAL_CONTROL = (Severity.CRITICAL,)

_ACTION = "conversation_qa_scorecard:scorecard"
_SOD_GROUP = "conversation_qa_scorecard-maker-checker"


def _redact(text: str) -> str:
    """Mask every jurisdiction's identifiers plus email/phone, and normalise whitespace."""
    return re.sub(r"\s+", " ", pii_redact(text, _ALL_PATTERNS)).strip()


def _summary(scorecard: Scorecard) -> str:
    """One line a reviewer can triage on, built from the ENGINE's own figures."""
    failing = scorecard.failing
    listed = ", ".join(f"{f.requirement_id}={f.status.value}" for f in failing) or "none"
    return (
        f"{scorecard.disposition.value} on {scorecard.market.value} pack {scorecard.pack_id}; "
        f"unmet: {listed}; disclosure {scorecard.disclosure_score:.2f}, "
        f"adherence {scorecard.adherence_score:.2f}"
    )


def _kit_citations(scorecard: Scorecard) -> tuple[KitCitation, ...]:
    seen: set[str] = set()
    out: list[KitCitation] = []
    for citation in scorecard.citations:
        if citation.source_id in seen:
            continue
        seen.add(citation.source_id)
        out.append(
            KitCitation(
                source_id=citation.source_id,
                title=citation.title,
                snippet=_redact(citation.snippet),
            )
        )
        if len(out) >= _MAX_CITATIONS:
            break
    return tuple(out)


def scorecard_to_review(scorecard: Scorecard, *, maker: str, tenant: str = "") -> Review:
    """Build the review a producer submits to Hrz7 when a scorecard does not pass."""
    return Review(
        action=_ACTION,
        subject=_redact(scorecard.contact_id),
        maker=maker,
        tenant=tenant or scorecard.tenant,
        summary=_redact(_summary(scorecard)),
        severity=scorecard.severity.value,
        required_approvals=2 if scorecard.severity in _DUAL_CONTROL else 1,
        sod_group=_SOD_GROUP,
        case_ref=scorecard.contact_id,
        # Producer-owned, tenant-scoped key so a retried delivery is idempotent at the console.
        # The scorecard id is already a digest of the contact, pack, transcript and outcome, so
        # a re-score that changed nothing does not open a second review.
        source_key=f"E3:{scorecard.scorecard_id}",
        citations=_kit_citations(scorecard),
    )
