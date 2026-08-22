"""The scoring path opens ONE span, and that span carries no content.

A trace backend is not the WORM audit trail. It has no redaction stage, no retention policy
written against a regulator's requirement, and a far wider read audience than the audit
store. So the value of tracing the scoring path depends entirely on the span carrying
structural attributes only: who, which tenant, which market, how long. A transcript
fragment, a customer identifier or a finding's text reaching a span has left the boundary
that ``redact_for_scoring`` exists to hold, and it has left it silently.

The PII case here uses the contact whose transcript carries a planted NRIC, so the check is
run against input that would actually leak if any attribute were content-shaped.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from conversation_qa_scorecard.config import Settings, build_container
from conversation_qa_scorecard.domain.scorecard_service import ScorecardService
from conversation_qa_scorecard.score_pack import pack_for_market

from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 3, 2, 9, 30, tzinfo=UTC)


class _RecordingTracer:
    """Captures every span name and attribute so the test can inspect what was emitted."""

    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, str]]] = []

    @contextmanager
    def span(self, name: str, **attributes: str):  # type: ignore[no-untyped-def]
        self.spans.append((name, dict(attributes)))
        yield

    def record_token_usage(self, usage: object, model: str) -> None:
        return None


def _score(contact_id: str) -> _RecordingTracer:
    settings = Settings.load()
    container = build_container(settings)
    tracer = _RecordingTracer()
    service = ScorecardService(
        audit=container.audit,
        transcripts=container.transcription,
        store=container.scorecard_store,
        review_router=container.review_router,
        tracer=tracer,  # type: ignore[arg-type]
    )
    source = container.transcription
    contact = next(c for c in source.contacts() if c.contact_id == contact_id)
    service.score_contact(
        contact,
        pack_for_market(settings, contact.market, contact.product),
        actor=sample_cases.ACTOR,
        tenant=contact.tenant,
        as_of=_AS_OF,
        export=False,
    )
    return tracer


def test_scoring_a_contact_opens_exactly_one_named_span() -> None:
    tracer = _score(sample_cases.COMPLIANT_CONTACT)
    assert [name for name, _ in tracer.spans] == ["scorecard.score_contact"]


def test_the_span_carries_the_structural_attributes_an_operator_needs() -> None:
    """Enough to answer "whose scoring is slow, and in which market", and nothing more."""
    _, attributes = _score(sample_cases.COMPLIANT_CONTACT).spans[0]
    assert attributes["action"] == "score_contact"
    assert attributes["actor"] == sample_cases.ACTOR
    assert attributes["tenant"] == sample_cases.TENANT
    assert attributes["market"]


def test_no_span_attribute_carries_transcript_content_or_a_planted_identifier() -> None:
    """The contact used here has an NRIC planted in its transcript, so a leak would show."""
    tracer = _score(sample_cases.PII_CONTACT)
    emitted = " ".join(value for _, attributes in tracer.spans for value in attributes.values())
    assert sample_cases.PLANTED_NRIC not in emitted
    assert sample_cases.PLANTED_NRIC.lower() not in emitted.lower()


@pytest.mark.parametrize(
    "contact_id", [sample_cases.COMPLIANT_CONTACT, sample_cases.BREACH_CONTACT]
)
def test_the_attribute_set_is_a_fixed_allowlist_whatever_the_verdict(contact_id: str) -> None:
    """A failing contact must not start attaching findings to the span to explain itself."""
    tracer = _score(contact_id)
    for _, attributes in tracer.spans:
        assert set(attributes) == {"action", "actor", "tenant", "market"}
