"""Assembly: build the domain service from the container, in one place.

The domain must not import ``config`` (it is pure and knows nothing about profiles or YAML),
and ``config`` must not import the domain service (it binds ports and stops there). This module
is the seam between them, so every surface (API, CLI, agent, demo, eval) constructs the service
identically instead of each wiring its own subset of ports and quietly omitting one.

Omitting one is not hypothetical: a surface that forgot the review router would still produce
correct scorecards and would silently stop honouring rule R8.
"""

from __future__ import annotations

from .config import Container
from .domain.ingestion import RedactedTranscript, redact_for_scoring
from .domain.models import ContactRecord, ScorePack
from .domain.pii import PII_PATTERNS
from .domain.scorecard_service import ScorecardService
from .score_pack import pack_for_market


def build_service(container: Container) -> ScorecardService:
    """Wire every port the scoring path needs. No surface may build a narrower one."""
    return ScorecardService(
        audit=container.audit,
        transcripts=container.transcription,
        store=container.scorecard_store,
        review_router=container.review_router,
        tracer=container.tracer,
        narrator=container.narration,
        classifier=container.signal_classifier,
        warehouse=container.warehouse,
    )


def pack_for_contact(container: Container, contact: ContactRecord) -> ScorePack:
    """The pack that scores this contact's market and product, or raise.

    Fail-closed by construction: ``pack_for_market`` refuses rather than falling back to
    another market's pack, because scoring a Japanese solicitation against Singapore's
    requirements produces a confident, cited and completely wrong scorecard.
    """
    return pack_for_market(container.settings, contact.market, contact.product)


def redacted_transcript_for(container: Container, contact: ContactRecord) -> RedactedTranscript:
    """Fetch and mask one contact's transcript, for a reviewer's timeline view.

    The SAME ingestion call the scoring path makes, so what a reviewer reads is exactly what
    the engine scored, character offsets included. A second, kinder rendering path is how a
    citation ends up pointing at different words than the finding it justifies.
    """
    raw = container.transcription.fetch(
        contact.contact_id, locale=contact.locale, audio_uri=contact.audio_uri
    )
    return redact_for_scoring(raw, PII_PATTERNS)
