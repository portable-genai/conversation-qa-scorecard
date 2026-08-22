"""The model may restate the scorecard and may not compute one, proved in both directions.

Two claims, and they need each other:

1. **Groundedness.** A draft that mentions a figure the engine never published, or cites an
   instrument the findings never named, is DISCARDED. The service falls back to prose composed
   by pure code, so a scorecard is never blocked on a model and never carries a number a model
   invented.
2. **Model-freedom of the consequential result.** Score the same contact with the narrator and
   the advisory classifier bound, then again with both stubbed out, and every field except the
   two advisory ones is byte-identical. That is what makes claim 1 worth having: if the model
   could move a number, rejecting its prose would be beside the point.

The second test is the one that would catch the drift nobody notices: somebody wires a model
into a threshold "just for the ambiguous cases", the eval still passes because the model is
usually right, and the determinism claim is quietly false.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from conversation_qa_scorecard.adapters.local.narration import (
    LocalNarrator,
)
from conversation_qa_scorecard.adapters.local.signals import (
    LocalSignalClassifier,
)
from conversation_qa_scorecard.adapters.local.transcription import (
    FixtureTranscriptSource,
)
from conversation_qa_scorecard.config import (
    Settings,
    build_container,
)
from conversation_qa_scorecard.domain.errors import (
    NarrationRejectedError,
)
from conversation_qa_scorecard.domain.ingestion import (
    redact_for_scoring,
)
from conversation_qa_scorecard.domain.kernel import (
    Citation,
)
from conversation_qa_scorecard.domain.models import (
    Narration,
    Scorecard,
    ScoringRequest,
)
from conversation_qa_scorecard.domain.narration import (
    allowed_figures,
    deterministic_narration,
    figures_in,
    grounded_or_fallback,
    narration_brief,
    validate_narration,
)
from conversation_qa_scorecard.domain.scorecard_service import (
    ScorecardService,
)
from conversation_qa_scorecard.domain.scoring_engine import (
    ScoringEngine,
)
from conversation_qa_scorecard.score_pack import (
    pack_for_market,
)

from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)
_SETTINGS = Settings(profile="local", audit_path=":memory:", scorecard_path=":memory:")
_SOURCE = FixtureTranscriptSource(_SETTINGS)


def _scorecard(contact_id: str) -> Scorecard:
    contact = next(c for c in _SOURCE.contacts() if c.contact_id == contact_id)
    redacted = redact_for_scoring(
        _SOURCE.fetch(contact.contact_id, locale=contact.locale, audio_uri=contact.audio_uri)
    )
    return ScoringEngine().score(
        ScoringRequest(
            contact=contact,
            pack=pack_for_market(_SETTINGS, contact.market, contact.product),
            as_of=_AS_OF,
            redaction_count=redacted.count,
        ),
        redacted.transcript,
    )


# --------------------------------------------------------------------------------------- #
# Groundedness
# --------------------------------------------------------------------------------------- #
def test_the_deterministic_fallback_passes_the_gate_it_is_the_fallback_for() -> None:
    """Otherwise the gate is not groundedness, it is the model switched off with extra steps."""
    scorecard = _scorecard(sample_cases.BREACH_CONTACT)
    fallback = deterministic_narration(scorecard)
    assert validate_narration(fallback, narration_brief(scorecard)) is not None


def test_a_draft_that_invents_a_figure_is_rejected_and_names_the_figure() -> None:
    scorecard = _scorecard(sample_cases.BREACH_CONTACT)
    draft = Narration(
        headline="All good",
        body="Adherence was 97 per cent across 1234 contacts.",
        model="fixture",
    )
    with pytest.raises(NarrationRejectedError) as caught:
        validate_narration(draft, narration_brief(scorecard))
    assert "97" in str(caught.value)


def test_a_draft_that_cites_an_instrument_the_findings_did_not_is_rejected() -> None:
    scorecard = _scorecard(sample_cases.BREACH_CONTACT)
    draft = replace(
        deterministic_narration(scorecard),
        citations=(Citation(source_id="invented_instrument", title="Nowhere"),),
    )
    with pytest.raises(NarrationRejectedError, match="invented_instrument"):
        validate_narration(draft, narration_brief(scorecard))


def test_a_rejected_draft_falls_back_rather_than_failing_the_assessment() -> None:
    """A compliance assessment is never blocked on a model being well behaved."""
    scorecard = _scorecard(sample_cases.BREACH_CONTACT)
    brief = narration_brief(scorecard)
    ungrounded = Narration(headline="x", body="Adherence was 97 per cent.", model="fixture")
    kept = grounded_or_fallback(ungrounded, brief, scorecard)
    assert kept.grounded is True
    assert "97" not in kept.body
    assert "rejected draft" in kept.model


def test_no_narrator_bound_still_produces_a_grounded_narration() -> None:
    scorecard = _scorecard(sample_cases.COMPLIANT_CONTACT)
    kept = grounded_or_fallback(None, narration_brief(scorecard), scorecard)
    assert kept.model == "deterministic"
    assert kept.body


def test_the_allowed_figure_set_is_derived_from_the_engine_and_carries_both_renderings() -> None:
    """A narrator saying "80%" for a 0.8 ratio is correct, and must not be rejected for it."""
    scorecard = _scorecard(sample_cases.BREACH_CONTACT)
    allowed = allowed_figures(scorecard)
    assert str(len(scorecard.findings)) in allowed
    assert figures_in(f"{scorecard.disclosure_score}") <= allowed
    assert figures_in(f"{scorecard.disclosure_score * 100:.0f}") <= allowed


def test_the_offline_narrator_is_grounded_by_construction() -> None:
    """The bound local adapter is the standing proof that the gate admits correct prose."""
    scorecard = _scorecard(sample_cases.BREACH_CONTACT)
    brief = narration_brief(scorecard)
    draft = LocalNarrator(_SETTINGS).narrate(brief)
    assert draft is not None
    assert validate_narration(draft, brief).body == draft.body


def test_the_brief_carries_no_transcript_text() -> None:
    """P-04: minimise what reaches a model. The brief is figures and identifiers, not speech."""
    scorecard = _scorecard(sample_cases.PII_CONTACT)
    brief = narration_brief(scorecard)
    rendered = repr(brief)
    assert sample_cases.PLANTED_NRIC not in rendered
    for finding in scorecard.findings:
        for span in finding.evidence:
            assert span.text not in rendered, "the brief must not carry utterances"


# --------------------------------------------------------------------------------------- #
# Model freedom of the consequential result
# --------------------------------------------------------------------------------------- #
_CONSEQUENTIAL_FIELDS = (
    "scorecard_id",
    "contact_id",
    "tenant",
    "market",
    "pack_id",
    "pack_version",
    "as_of",
    "transcript_id",
    "disposition",
    "decision",
    "severity",
    "requires_human_review",
    "findings",
    "signals",
    "disclosure_score",
    "adherence_score",
    "sentiment_score",
    "citations",
    "engine_version",
    "turn_count",
    "redaction_count",
)


def _service(*, with_model: bool) -> ScorecardService:
    container = build_container(_SETTINGS)
    return ScorecardService(
        audit=container.audit,
        transcripts=container.transcription,
        store=container.scorecard_store,
        review_router=container.review_router,
        tracer=container.tracer,
        narrator=container.narration if with_model else None,
        classifier=container.signal_classifier if with_model else None,
        warehouse=container.warehouse,
    )


@pytest.mark.parametrize(
    "contact_id",
    [
        sample_cases.COMPLIANT_CONTACT,
        sample_cases.BREACH_CONTACT,
        sample_cases.PII_CONTACT,
        sample_cases.UNTIMED_CONTACT,
        sample_cases.GAP_CONTACT,
    ],
)
def test_the_scorecard_is_identical_with_the_model_adapters_stubbed_out(contact_id: str) -> None:
    """Every consequential field, byte for byte, with and without a model in the loop."""
    contact = next(c for c in _SOURCE.contacts() if c.contact_id == contact_id)
    pack = pack_for_market(_SETTINGS, contact.market, contact.product)

    with_model = _service(with_model=True).score_contact(
        contact, pack, actor="eval-bot", tenant=contact.tenant, as_of=_AS_OF, export=False
    )
    without = _service(with_model=False).score_contact(
        contact, pack, actor="eval-bot", tenant=contact.tenant, as_of=_AS_OF, export=False
    )

    for field in _CONSEQUENTIAL_FIELDS:
        assert getattr(with_model, field) == getattr(without, field), (
            f"{field} moved when the model adapters were bound; the model may only narrate"
        )


def test_only_the_advisory_fields_differ_when_a_model_is_bound() -> None:
    """The positive control: without it the test above is satisfied by a model that never runs."""
    contact = next(c for c in _SOURCE.contacts() if c.contact_id == sample_cases.PII_CONTACT)
    pack = pack_for_market(_SETTINGS, contact.market, contact.product)
    with_model = _service(with_model=True).score_contact(
        contact, pack, actor="eval-bot", tenant=contact.tenant, as_of=_AS_OF, export=False
    )
    without = _service(with_model=False).score_contact(
        contact, pack, actor="eval-bot", tenant=contact.tenant, as_of=_AS_OF, export=False
    )
    assert with_model.advisory and not without.advisory
    assert with_model.narration is not None and without.narration is not None
    assert with_model.narration.model != without.narration.model


def test_an_advisory_note_carries_nothing_that_could_be_promoted_to_a_decision() -> None:
    """No severity, no polarity, no requirement id. There is nothing in it to act on."""
    classifier = LocalSignalClassifier(_SETTINGS)
    from conversation_qa_scorecard.ports.signals import SignalRequest

    notes = classifier.classify(
        SignalRequest(
            contact_id=sample_cases.PII_CONTACT,
            locale="en-SG",
            customer_utterances=("I have lost my job",),
            detected_cue_ids=("financial_hardship",),
        )
    )
    assert notes
    for note in notes:
        assert not hasattr(note, "severity")
        assert not hasattr(note, "requirement_id")
        assert "advisory only" in note.text.lower()


def test_a_narrator_that_raises_does_not_fail_the_assessment() -> None:
    """A model outage is not a compliance outage: the verdict was complete before the call."""

    class _BrokenNarrator:
        def narrate(self, brief: object) -> Narration | None:
            raise RuntimeError("the model gateway is unreachable")

    container = build_container(_SETTINGS)
    service = ScorecardService(
        audit=container.audit,
        transcripts=container.transcription,
        store=container.scorecard_store,
        review_router=container.review_router,
        tracer=container.tracer,
        narrator=_BrokenNarrator(),  # type: ignore[arg-type]
        classifier=None,
        warehouse=None,
    )
    contact = next(c for c in _SOURCE.contacts() if c.contact_id == sample_cases.BREACH_CONTACT)
    scorecard = service.score_contact(
        contact,
        pack_for_market(_SETTINGS, contact.market, contact.product),
        actor="eval-bot",
        tenant=contact.tenant,
        as_of=_AS_OF,
    )
    assert scorecard.narration is not None
    assert scorecard.narration.model == "deterministic"
    assert scorecard.requires_human_review is True
