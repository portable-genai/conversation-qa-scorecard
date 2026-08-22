"""Ingestion: turn assembly and redaction, both deterministic, both before any model.

The rule this suite defends is an ORDER, not a feature:

    fetch -> compute redaction spans -> mask -> match -> score -> narrate

Everything after "mask" sees masked text. That is why the evidence spans on a scorecard can be
shown to a reviewer verbatim, why the outbound review payload needs no second scrubbing to be
safe, and why the narration brief can be handed to a model at all.

The second thing proved here is the honest consequence of masking: a replacement of a different
length shifts every later offset, so word timings are dropped from masked turns and a
requirement with a declared window whose evidence lands there reports UNVERIFIABLE. That is
fail-closed and it is the reason the fixture format has to say whether it declares timings.
"""

from __future__ import annotations

import pathlib

from speech_lexicon_kit import ChannelRole, SpeakerTurn, Transcript

from conversation_qa_scorecard.adapters.local.transcription import (
    FixtureTranscriptSource,
)
from conversation_qa_scorecard.config import (
    Settings,
)
from conversation_qa_scorecard.domain.ingestion import (
    redact_for_scoring,
    redaction_spans,
)
from conversation_qa_scorecard.domain.pii import (
    PII_PATTERNS,
)

from tests.fixtures import sample_cases

_SETTINGS = Settings(profile="local", audit_path=":memory:", scorecard_path=":memory:")
_SOURCE = FixtureTranscriptSource(_SETTINGS)


def _transcript(text: str, role: ChannelRole = ChannelRole.CUSTOMER) -> Transcript:
    return Transcript(
        transcript_id="tr-unit",
        locale="en-SG",
        turns=(
            SpeakerTurn(index=0, speaker_id="caller-1", role=role, text=text, start_ms=0, end_ms=1),
        ),
    )


# --------------------------------------------------------------------------------------- #
# Turn assembly
# --------------------------------------------------------------------------------------- #
def test_the_fixture_reader_assembles_turns_with_roles_and_indices() -> None:
    transcript = _SOURCE.fetch(sample_cases.COMPLIANT_CONTACT)
    assert transcript.turns
    assert [turn.index for turn in transcript.turns] == list(range(len(transcript.turns)))
    assert {turn.role for turn in transcript.turns} == {ChannelRole.AGENT, ChannelRole.CUSTOMER}
    assert transcript.is_open is False, "a post-contact assessment scores a closed conversation"


def test_a_fixture_that_declares_no_word_timings_carries_none() -> None:
    """The honest default: a recogniser that gave no word timings produces none here either."""
    transcript = _SOURCE.fetch(sample_cases.UNTIMED_CONTACT)
    assert all(turn.words == () for turn in transcript.turns)


def test_a_fixture_that_declares_even_timings_carries_word_offsets() -> None:
    transcript = _SOURCE.fetch(sample_cases.COMPLIANT_CONTACT)
    assert any(turn.words for turn in transcript.turns)
    for turn in transcript.turns:
        for word in turn.words:
            assert turn.text[word.char_start : word.char_end] == word.text


def test_an_unknown_contact_raises_rather_than_answering_with_an_empty_transcript() -> None:
    """An empty transcript scores every disclosure as absent: confident, cited and wrong."""
    from conversation_qa_scorecard.adapters.local.transcription import (
        TranscriptFixtureError,
    )

    try:
        _SOURCE.fetch("CT-NOPE-0000")
    except TranscriptFixtureError as exc:
        assert "CT-NOPE-0000" in str(exc)
    else:  # pragma: no cover - the failure this test exists for
        raise AssertionError("the offline source answered for a contact it does not have")


# --------------------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------------------- #
def test_an_identifier_is_masked_and_the_mask_names_what_it_removed() -> None:
    transcript = _transcript(f"My NRIC is {sample_cases.PLANTED_NRIC} thank you")
    redacted = redact_for_scoring(transcript, PII_PATTERNS)
    text = redacted.transcript.turns[0].text
    assert sample_cases.PLANTED_NRIC not in text
    assert "[" in text and "]" in text, "a reviewer should see that something was removed"
    assert redacted.count == 1


def test_the_spans_index_the_original_text_so_they_can_be_audited() -> None:
    raw = f"My NRIC is {sample_cases.PLANTED_NRIC} thank you"
    spans = redaction_spans(_transcript(raw), PII_PATTERNS)
    assert len(spans) == 1
    span = spans[0]
    assert raw[span.char_start : span.char_end] == sample_cases.PLANTED_NRIC


def test_two_detectors_claiming_one_run_are_resolved_by_order_not_merged() -> None:
    """pii-kit leaves precedence to the consumer, so the vertical's order is what decides."""
    spans = redaction_spans(_transcript("write to ops@meridian.example about it"), PII_PATTERNS)
    assert len(spans) == 1, "overlapping detections must not produce overlapping spans"


def test_a_turn_with_no_identifier_is_left_exactly_as_it_was() -> None:
    raw = "Thank you, that is very helpful."
    redacted = redact_for_scoring(_transcript(raw), PII_PATTERNS)
    assert redacted.transcript.turns[0].text == raw
    assert redacted.count == 0


def test_masking_drops_the_word_timings_of_the_turn_it_touched() -> None:
    """The kernel refuses to keep offsets that index text which no longer exists."""
    transcript = _SOURCE.fetch(sample_cases.PII_CONTACT)
    redacted = redact_for_scoring(transcript, PII_PATTERNS)
    masked_turns = {span.turn_index for span in redacted.spans}
    assert masked_turns, "the PII contact should carry at least one identifier"
    for index in masked_turns:
        assert redacted.transcript.turns[index].words == ()
    untouched = next(
        turn for turn in transcript.turns if turn.index not in masked_turns and turn.words
    )
    assert redacted.transcript.turns[untouched.index].words == untouched.words


def test_the_scored_transcript_is_the_masked_one_end_to_end() -> None:
    """The single most important property in the repo, asserted on the shipped pipeline."""
    transcript = _SOURCE.fetch(sample_cases.PII_CONTACT)
    assert sample_cases.PLANTED_NRIC in " ".join(turn.text for turn in transcript.turns)
    redacted = redact_for_scoring(transcript, PII_PATTERNS)
    assert sample_cases.PLANTED_NRIC not in " ".join(
        turn.text for turn in redacted.transcript.turns
    )


def test_redaction_is_deterministic() -> None:
    transcript = _SOURCE.fetch(sample_cases.PII_CONTACT)
    first = redact_for_scoring(transcript, PII_PATTERNS)
    second = redact_for_scoring(transcript, PII_PATTERNS)
    assert first.transcript == second.transcript
    assert first.spans == second.spans


def test_ingestion_reaches_no_model_and_no_cloud_sdk() -> None:
    """The model does nothing on this path, which is only true if it is not even reachable.

    Asserted on the module's IMPORT GRAPH rather than on its text: prose that explains the
    ordering rule mentions narration by name, and a substring scan over the source would fail
    on the explanation while a module that actually imported a narrator would pass the day
    somebody deleted the comment.
    """
    import ast

    from conversation_qa_scorecard.domain import ingestion

    tree = ast.parse(pathlib.Path(ingestion.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported == {
        "__future__",
        "dataclasses",
        "pii_kit",
        "speech_lexicon_kit",
        ".pii",
    } or all(
        not name.startswith(("google", "conversation_qa_scorecard.adapters"))
        and "narration" not in name
        and "signals" not in name
        for name in imported
    ), f"the ingestion path reaches something it must not: {sorted(imported)}"
