"""The deterministic scoring engine: pure, replayable, fail-closed, and citing every verdict.

The engine owns every number and every verdict on this service, so this is the suite that has
to be right. It is organised around the six ways the engine refuses to guess, because each of
them is a scorecard that would otherwise say "compliant" about a contact nobody measured:

1. an unmatched sequence is ABSENT;
2. an out-of-order one is OUT_OF_ORDER, and scores exactly as absent does;
3. a declared timing window over a transcript with no word timings is UNVERIFIABLE;
4. a declared obligation the pack does not configure is a GAP;
5. a contact with no declared obligations is INDETERMINATE at 0.0, not vacuously compliant;
6. a wording said by the wrong speaker does not count.

Plus the two properties everything else rests on: purity (same inputs, same output, including
the identifier) and citation (every satisfied finding names the turn and the characters).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from speech_lexicon_kit import ChannelRole, SpeakerTurn, Transcript

from conversation_qa_scorecard.adapters.local.transcription import (
    FixtureTranscriptSource,
)
from conversation_qa_scorecard.config import (
    Settings,
)
from conversation_qa_scorecard.domain.ingestion import (
    redact_for_scoring,
)
from conversation_qa_scorecard.domain.kernel import (
    Decision,
    Severity,
)
from conversation_qa_scorecard.domain.models import (
    SEVERITY_RANK,
    ContactRecord,
    Disposition,
    Market,
    RequirementKind,
    RequirementStatus,
    Scorecard,
    ScorePack,
    ScoringRequest,
    SignalKind,
)
from conversation_qa_scorecard.domain.scoring_engine import (
    GAP_SEVERITY,
    ScoringEngine,
)
from conversation_qa_scorecard.score_pack import (
    load_packs,
    pack_for_market,
)

from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)
_SETTINGS = Settings(profile="local", audit_path=":memory:", scorecard_path=":memory:")
_SOURCE = FixtureTranscriptSource(_SETTINGS)
_CONTACTS = {contact.contact_id: contact for contact in _SOURCE.contacts()}


def score(contact_id: str, *, as_of: datetime = _AS_OF) -> Scorecard:
    """Run the real pipeline over one shipped fixture."""
    contact = _CONTACTS[contact_id]
    redacted = redact_for_scoring(
        _SOURCE.fetch(contact.contact_id, locale=contact.locale, audio_uri=contact.audio_uri)
    )
    return ScoringEngine().score(
        ScoringRequest(
            contact=contact,
            pack=pack_for_market(_SETTINGS, contact.market, contact.product),
            as_of=as_of,
            redaction_count=redacted.count,
        ),
        redacted.transcript,
    )


def status(scorecard: Scorecard, requirement_id: str) -> RequirementStatus:
    return next(f.status for f in scorecard.findings if f.requirement_id == requirement_id)


# --------------------------------------------------------------------------------------- #
# The happy path, so every refusal below means something
# --------------------------------------------------------------------------------------- #
def test_a_clean_contact_is_compliant_and_escalates_nothing() -> None:
    scorecard = score(sample_cases.COMPLIANT_CONTACT)
    assert scorecard.disposition is Disposition.COMPLIANT
    assert scorecard.decision is Decision.ALLOWED
    assert scorecard.requires_human_review is False
    assert scorecard.disclosure_score == 1.0
    assert scorecard.adherence_score == 1.0
    assert all(finding.satisfied for finding in scorecard.findings)


def test_every_satisfied_finding_cites_a_turn_and_a_character_span() -> None:
    """A verdict with no evidence is not reviewable, and a reviewer is the whole product."""
    scorecard = score(sample_cases.COMPLIANT_CONTACT)
    transcript = _SOURCE.fetch(sample_cases.COMPLIANT_CONTACT)
    for finding in scorecard.findings:
        assert finding.evidence, f"{finding.requirement_id} passed with no evidence"
        for span in finding.evidence:
            turn = transcript.turns[span.turn_index]
            assert span.char_end > span.char_start
            assert turn.text[span.char_start : span.char_end] == span.text
            assert span.role is ChannelRole.AGENT, "a disclosure must be cited to the agent"


def test_a_matched_phrase_is_cited_to_the_words_that_matched_not_the_whole_turn() -> None:
    scorecard = score(sample_cases.COMPLIANT_CONTACT)
    recording = next(f for f in scorecard.findings if f.requirement_id == "SG-DISC-RECORDING")
    span = recording.evidence[0]
    assert span.text == "this call may be recorded"


# --------------------------------------------------------------------------------------- #
# Fail-closed, one refusal at a time
# --------------------------------------------------------------------------------------- #
def test_an_unsaid_disclosure_is_absent_and_the_contact_is_non_compliant() -> None:
    scorecard = score(sample_cases.BREACH_CONTACT)
    assert status(scorecard, "SG-DISC-RISK") is RequirementStatus.ABSENT
    assert scorecard.disposition is Disposition.NON_COMPLIANT
    assert scorecard.severity is Severity.CRITICAL
    assert scorecard.requires_human_review is True
    absent = next(f for f in scorecard.findings if f.requirement_id == "SG-DISC-RISK")
    assert absent.evidence == (), "an absence must not carry evidence"
    assert absent.remediation, "a failure must say what to do about it"


def test_consent_taken_before_the_disclosures_is_out_of_order_and_fails() -> None:
    """Every step was said. A fee disclosure after the customer agreed is not a fee disclosure."""
    scorecard = score(sample_cases.OUT_OF_ORDER_CONTACT)
    assert status(scorecard, "SG-SCRIPT-ADVICE-ORDER") is RequirementStatus.OUT_OF_ORDER
    assert scorecard.disposition is Disposition.NON_COMPLIANT
    assert scorecard.adherence_score == 0.0, "out of order scores exactly as absent does"


def test_a_declared_window_over_a_transcript_with_no_timings_is_unverifiable() -> None:
    """An unchecked deadline is never reported as a met one."""
    scorecard = score(sample_cases.UNTIMED_CONTACT)
    assert status(scorecard, "SG-DISC-RECORDING") is RequirementStatus.UNVERIFIABLE
    assert status(scorecard, "SG-DISC-IDENTITY") is RequirementStatus.UNVERIFIABLE
    assert status(scorecard, "SG-DISC-RISK") is RequirementStatus.PRESENT, (
        "an UNTIMED requirement is still checkable, so unverifiable must not spread"
    )
    assert scorecard.disposition is Disposition.INDETERMINATE
    assert scorecard.requires_human_review is True


def test_a_disclosure_outside_its_window_is_late_and_still_fails() -> None:
    scorecard = score(sample_cases.LATE_CONTACT)
    assert status(scorecard, "SG-DISC-RECORDING") is RequirementStatus.LATE
    assert scorecard.disposition is Disposition.NON_COMPLIANT


def test_an_obligation_the_pack_does_not_configure_is_a_gap_at_the_top_severity() -> None:
    """ "We did not check" looks exactly like "nothing was wrong" on every dashboard."""
    scorecard = score(sample_cases.GAP_CONTACT)
    gap = next(f for f in scorecard.findings if f.requirement_id == "SG-DISC-VULNERABLE-SUPPORT")
    assert gap.status is RequirementStatus.GAP
    assert gap.severity is GAP_SEVERITY
    assert gap.kind is RequirementKind.UNCONFIGURED
    assert scorecard.disposition is Disposition.INDETERMINATE
    assert gap.remediation, "a gap must tell somebody how to close it"


def test_a_gap_drags_down_both_scores_because_nobody_knows_which_it_was() -> None:
    clean = score(sample_cases.COMPLIANT_CONTACT)
    with_gap = score(sample_cases.GAP_CONTACT)
    assert with_gap.disclosure_score < clean.disclosure_score
    assert with_gap.adherence_score < clean.adherence_score


def test_a_contact_with_no_declared_obligations_is_indeterminate_at_zero() -> None:
    """An empty requirement set is silence, and silence must not look like compliance."""
    contact = replace(_CONTACTS[sample_cases.COMPLIANT_CONTACT], declared_requirement_ids=())
    redacted = redact_for_scoring(_SOURCE.fetch(contact.contact_id))
    scorecard = ScoringEngine().score(
        ScoringRequest(
            contact=contact,
            pack=pack_for_market(_SETTINGS, contact.market, contact.product),
            as_of=_AS_OF,
        ),
        redacted.transcript,
    )
    assert scorecard.findings == ()
    assert scorecard.disposition is Disposition.INDETERMINATE
    assert scorecard.disclosure_score == 0.0
    assert scorecard.adherence_score == 0.0
    assert scorecard.requires_human_review is True


def test_a_wording_said_by_the_customer_does_not_satisfy_the_agents_obligation() -> None:
    """The role binding is the difference between a disclosure and the customer repeating it."""
    contact = _CONTACTS[sample_cases.COMPLIANT_CONTACT]
    transcript = _SOURCE.fetch(contact.contact_id)
    flipped = Transcript(
        transcript_id=transcript.transcript_id,
        locale=transcript.locale,
        turns=tuple(
            SpeakerTurn(
                index=turn.index,
                speaker_id=turn.speaker_id,
                role=ChannelRole.CUSTOMER,
                text=turn.text,
                start_ms=turn.start_ms,
                end_ms=turn.end_ms,
                channel=turn.channel,
                words=turn.words,
            )
            for turn in transcript.turns
        ),
        started_at=transcript.started_at,
        ended_at=transcript.ended_at,
        audio_duration_ms=transcript.audio_duration_ms,
        engine=transcript.engine,
    )
    scorecard = ScoringEngine().score(
        ScoringRequest(
            contact=contact,
            pack=pack_for_market(_SETTINGS, contact.market, contact.product),
            as_of=_AS_OF,
        ),
        flipped,
    )
    assert all(not finding.satisfied for finding in scorecard.findings)
    assert scorecard.disposition is Disposition.NON_COMPLIANT


# --------------------------------------------------------------------------------------- #
# Purity and stability
# --------------------------------------------------------------------------------------- #
def test_the_same_inputs_produce_the_same_scorecard_including_its_identifier() -> None:
    """Replayable byte for byte, which is what makes a re-score idempotent at the console."""
    first = score(sample_cases.BREACH_CONTACT)
    second = score(sample_cases.BREACH_CONTACT)
    assert first == second
    assert first.scorecard_id == second.scorecard_id


def test_as_of_is_a_parameter_so_a_replay_reproduces_the_original_verdict() -> None:
    """The engine holds no clock. A different assessment time is a different, explicit input."""
    later = score(sample_cases.BREACH_CONTACT, as_of=_AS_OF + timedelta(days=365))
    now = score(sample_cases.BREACH_CONTACT)
    assert later.disposition is now.disposition
    assert later.findings == now.findings
    assert later.scorecard_id != now.scorecard_id, "the id records WHEN it was assessed"


def test_findings_are_ordered_failing_first_then_worst_severity_then_id() -> None:
    """A stable total order, so two runs render the same rows in the same places."""
    scorecard = score(sample_cases.BREACH_CONTACT)
    keys = [
        (finding.satisfied, SEVERITY_RANK[finding.severity], finding.requirement_id)
        for finding in scorecard.findings
    ]
    assert keys == sorted(keys)


def test_the_engine_holds_no_clock_and_refuses_a_naive_as_of() -> None:
    """A naive timestamp compares differently on every host, so the kernel refuses it."""
    contact = _CONTACTS[sample_cases.COMPLIANT_CONTACT]
    with pytest.raises(ValueError, match="timezone-aware"):
        ScoringEngine().score(
            ScoringRequest(
                contact=contact,
                pack=pack_for_market(_SETTINGS, contact.market, contact.product),
                as_of=datetime(2026, 7, 20, 4, 0),
            ),
            _SOURCE.fetch(contact.contact_id),
        )


# --------------------------------------------------------------------------------------- #
# The engine knows no market
# --------------------------------------------------------------------------------------- #
def test_the_same_engine_scores_a_different_market_with_no_code_change() -> None:
    australia = score(sample_cases.AU_CONTACT)
    japan = score(sample_cases.JP_CONTACT)
    assert australia.market is Market.AU
    assert australia.disposition is Disposition.NON_COMPLIANT
    assert japan.market is Market.JP
    assert japan.disposition is Disposition.COMPLIANT, (
        "Japanese has no word separators; matching runs over the character run"
    )


def test_a_market_with_no_configured_pack_refuses_rather_than_borrowing_another() -> None:
    """Scoring one market against another's pack produces a confident and wrong scorecard."""
    from conversation_qa_scorecard.domain.errors import ScorePackError

    lonely = replace(_SETTINGS, score_pack_path="")
    with pytest.raises(ScorePackError, match="no pack is configured"):
        pack_for_market(lonely, Market.SG, "a_product_line_nobody_configured")


def test_the_engine_carries_no_market_vocabulary_of_its_own() -> None:
    """Practice B4: the wordings, severities and windows are data, not constants in the code."""
    import inspect

    from conversation_qa_scorecard.domain import scoring_engine

    source = inspect.getsource(scoring_engine)
    for market_word in ("Singapore", "Australia", "MAS", "ASIC", "recording notice"):
        assert market_word not in source.replace(scoring_engine.__doc__ or "", ""), (
            f"{market_word!r} is policy and belongs in the score pack, not in the engine"
        )


# --------------------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------------------- #
def test_a_vulnerability_cue_is_consequential_on_its_own() -> None:
    scorecard = score(sample_cases.PII_CONTACT)
    assert "financial_hardship" in scorecard.vulnerability_cue_ids
    assert all(finding.satisfied for finding in scorecard.findings)
    assert scorecard.disposition is Disposition.REMEDIATE, (
        "every obligation was met, and a hardship signal still needs a human to look"
    )
    assert scorecard.requires_human_review is True


def test_a_cue_carries_the_span_that_evidences_it() -> None:
    scorecard = score(sample_cases.PII_CONTACT)
    hardship = next(s for s in scorecard.signals if s.cue_id == "financial_hardship")
    assert hardship.kind is SignalKind.VULNERABILITY
    assert hardship.evidence and hardship.evidence[0].role is ChannelRole.CUSTOMER


def test_the_sentiment_score_is_the_sum_of_the_polarities_the_pack_declares() -> None:
    scorecard = score(sample_cases.COMPLIANT_CONTACT)
    expected = sum(signal.polarity for signal in scorecard.signals if signal.detected)
    assert scorecard.sentiment_score == expected


def test_a_pack_is_a_value_the_engine_takes_rather_than_something_it_looks_up() -> None:
    """Hand it a different pack and it scores differently, with no configuration involved."""
    pack: ScorePack = load_packs()[sample_cases.SG_PACK_ID]
    trimmed = replace(pack, requirements=pack.requirements[:1])
    contact = ContactRecord(
        contact_id=sample_cases.COMPLIANT_CONTACT,
        tenant=sample_cases.TENANT,
        market=Market.SG,
        product=pack.product,
        declared_requirement_ids=("SG-DISC-RECORDING",),
    )
    scorecard = ScoringEngine().score(
        ScoringRequest(contact=contact, pack=trimmed, as_of=_AS_OF),
        _SOURCE.fetch(sample_cases.COMPLIANT_CONTACT),
    )
    assert [f.requirement_id for f in scorecard.findings] == ["SG-DISC-RECORDING"]
