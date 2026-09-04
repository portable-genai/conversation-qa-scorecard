"""Rule R8: a failing scorecard is ROUTED to human-review-console, not left in a per-repo boolean.

This is the standing gate for the failure the rule exists to prevent. A repo can set
``requires_human_review = True``, pass every other test, and still auto-execute in practice
because nothing ever reads the flag. So the assertions here are about the ROUTING, not the
flag: a failing scorecard produces an outbound review, a passing one produces none, the payload
leaves redacted, and the on-prem placeholder refuses rather than swallowing the escalation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from conversation_qa_scorecard.adapters.gcp.review_router import (
    CloudReviewRouter,
)
from conversation_qa_scorecard.adapters.local.review_router import (
    LocalReviewRouter,
)
from conversation_qa_scorecard.adapters.local.transcription import (
    FixtureTranscriptSource,
)
from conversation_qa_scorecard.adapters.onprem.review_router import (
    OnPremReviewRouter,
)
from conversation_qa_scorecard.api.app import (
    app,
)
from conversation_qa_scorecard.config import (
    Settings,
)
from conversation_qa_scorecard.domain.ingestion import (
    redact_for_scoring,
)
from conversation_qa_scorecard.domain.models import (
    Scorecard,
    ScoringRequest,
)
from conversation_qa_scorecard.domain.scoring_engine import (
    ScoringEngine,
)
from conversation_qa_scorecard.score_pack import (
    pack_for_market,
)

from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)


def _settings(profile: str = "local") -> Settings:
    return Settings(
        profile=profile,
        audit_path=":memory:",
        scorecard_path=":memory:",
        tenant=sample_cases.TENANT,
    )


def _scorecard(contact_id: str) -> Scorecard:
    """Score one shipped fixture with the real engine. No hand-written verdicts here."""
    settings = _settings()
    source = FixtureTranscriptSource(settings)
    contact = next(c for c in source.contacts() if c.contact_id == contact_id)
    pack = pack_for_market(settings, contact.market, contact.product)
    redacted = redact_for_scoring(
        source.fetch(contact.contact_id, locale=contact.locale, audio_uri=contact.audio_uri)
    )
    return ScoringEngine().score(
        ScoringRequest(contact=contact, pack=pack, as_of=_AS_OF, redaction_count=redacted.count),
        redacted.transcript,
    )


def test_a_failing_scorecard_produces_an_outbound_review() -> None:
    router = LocalReviewRouter(_settings())
    scorecard = _scorecard(sample_cases.BREACH_CONTACT)
    ref = router.route(scorecard, maker=sample_cases.ACTOR)
    assert ref, "routing must return a reference, so the caller can record where it went"
    pending = router.outbox.pending()
    assert len(pending) == 1
    review = pending[0].review
    assert review.maker == sample_cases.ACTOR
    assert review.tenant == sample_cases.TENANT
    assert review.severity == scorecard.severity.value
    assert review.source_key, "a durable outbox needs an idempotency key"


def test_a_critical_scorecard_demands_dual_control() -> None:
    router = LocalReviewRouter(_settings())
    router.route(_scorecard(sample_cases.BREACH_CONTACT), maker=sample_cases.ACTOR)
    assert router.outbox.pending()[0].review.required_approvals == 2


def test_the_routing_key_is_stable_so_a_retry_does_not_open_a_second_review() -> None:
    """The scorecard id is a digest of the inputs and the outcome, so a re-score is idempotent."""
    first = _scorecard(sample_cases.BREACH_CONTACT)
    second = _scorecard(sample_cases.BREACH_CONTACT)
    router = LocalReviewRouter(_settings())
    router.route(first, maker=sample_cases.ACTOR)
    router.route(second, maker=sample_cases.ACTOR)
    keys = {entry.review.source_key for entry in router.outbox.pending()}
    assert len(keys) == 1, "two reviews for one unchanged assessment is a reviewer's nightmare"


def test_the_payload_is_redacted_before_it_leaves_the_process() -> None:
    """human-review-console is a shared sink; a raw identifier must never reach the wire."""
    router = LocalReviewRouter(_settings())
    router.route(_scorecard(sample_cases.PII_CONTACT), maker=sample_cases.ACTOR)
    wire = repr(router.outbox.pending()[0].review.to_payload())
    assert sample_cases.PLANTED_NRIC not in wire


def test_the_managed_router_refuses_when_no_console_is_configured() -> None:
    """An escalation with nowhere to go must fail loudly, not return as if it were reviewed."""
    router = CloudReviewRouter(Settings(profile="gcp", audit_path=":memory:", review_url=""))
    with pytest.raises(RuntimeError, match="R8"):
        router.route(_scorecard(sample_cases.BREACH_CONTACT), maker=sample_cases.ACTOR)


def test_the_onprem_placeholder_refuses_rather_than_dropping_the_escalation() -> None:
    router = OnPremReviewRouter(_settings("onprem"))
    with pytest.raises(NotImplementedError, match="R8"):
        router.route(_scorecard(sample_cases.BREACH_CONTACT), maker=sample_cases.ACTOR)


def test_the_api_routes_the_escalation_in_the_same_request() -> None:
    """The serving path, not just the adapter: an escalation must not depend on a later job."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    failing = client.post(
        "/v1/scorecards",
        json={"contact_id": sample_cases.BREACH_CONTACT},
        headers={"X-Dev-Persona": "auditor"},
    ).json()
    assert failing["requires_human_review"] is True
    assert failing["review_ref"], "an escalation with no routing reference went nowhere"

    passing = client.post(
        "/v1/scorecards",
        json={"contact_id": sample_cases.COMPLIANT_CONTACT},
        headers={"X-Dev-Persona": "auditor"},
    ).json()
    assert passing["requires_human_review"] is False
    assert passing["review_ref"] == "", "a passing scorecard must not manufacture a review"
