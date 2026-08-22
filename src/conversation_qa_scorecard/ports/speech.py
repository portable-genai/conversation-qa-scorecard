"""The speech boundary: the shared kernel's ports and types, RE-EXPORTED, never redeclared.

``speech-lexicon-kit`` is pinned by tag and owns the transcript vocabulary: the speech-to-text,
text-to-speech and diarization ports, and the ``Transcript`` / ``SpeakerTurn`` / ``WordOffset``
/ ``ChannelRole`` / ``RedactionSpan`` types. This module re-exports them so this repo still has
ONE import site for its hexagon boundary, exactly as it does for the identity port from
``hex-service-kit``.

Why re-export rather than redeclare
-----------------------------------
Three sibling systems (this scorecard, the contact-centre copilot and the comms-surveillance
investigator) have to agree on what "the agent read the recording disclosure at 00:12 of turn
4" means. A local copy of ``SpeakerTurn`` diverges the first time one repo adds a field, and
then a compliance answer depends on which repo you asked, which is not an answer. The kernel is
therefore installed, not vendored, and this file is a re-export with no type of its own.

What this repo DOES own, and deliberately keeps out of the kit:

* the PHRASES (score packs: mandated wordings, accepted paraphrases, ordering, timing windows,
  severities and citations), because a required wording is reviewed vertical policy on a
  per-market schedule and must not need a release of a shared package to change;
* the per-vertical CUE LEXICONS for sentiment and vulnerability, for the same reason;
* the ADAPTERS: managed batch transcription, the offline fixture reader and the on-premises
  placeholder all live here, because the kit is stdlib-only and opens no socket.

:class:`TranscriptSourcePort` below is this repo's own port and is NOT from the kit: it is the
seam that turns a contact reference into a transcript, whether by calling a recogniser or by
reading one that already exists. ``SpeechToTextPort`` is the narrower recogniser contract the
managed adapter satisfies underneath it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from speech_lexicon_kit import (
    AudioRef,
    ChannelRole,
    ChannelRoleBinding,
    DiarizationPort,
    DiarizationRequest,
    DiarizationResult,
    LexiconHit,
    RedactionSpan,
    SpeakerSegment,
    SpeakerTurn,
    SpeechSynthesisRequest,
    SpeechToTextPort,
    SynthesisResult,
    TextToSpeechPort,
    Transcript,
    TranscriptionRequest,
    TranscriptionResult,
    WordOffset,
)


@runtime_checkable
class TranscriptSourcePort(Protocol):
    """Turn a contact reference into a redactable transcript.

    The one seam every profile binds. It is deliberately wider than ``SpeechToTextPort``: a
    post-contact QA programme reads most of its transcripts from a store that already holds
    them, and only sends audio to a recogniser when it does not. Both answers are a
    :class:`Transcript`, so the deterministic half of the pipeline never learns which happened.

    The model does NOTHING on this path. Turn assembly, channel-to-role binding and word
    offsets come from the recogniser or the fixture; redaction spans are computed by pure code
    afterwards. No text reaches a model before it has been redacted.
    """

    def fetch(self, contact_id: str, *, locale: str = "", audio_uri: str = "") -> Transcript:
        """Return the transcript for ``contact_id``.

        Raises rather than returning an empty transcript when none can be produced: an empty
        transcript scores every disclosure as absent, which is a confident and wrong verdict
        about a contact nobody actually measured.
        """
        ...


__all__ = [
    "AudioRef",
    "ChannelRole",
    "ChannelRoleBinding",
    "DiarizationPort",
    "DiarizationRequest",
    "DiarizationResult",
    "LexiconHit",
    "RedactionSpan",
    "SpeakerSegment",
    "SpeakerTurn",
    "SpeechSynthesisRequest",
    "SpeechToTextPort",
    "SynthesisResult",
    "TextToSpeechPort",
    "Transcript",
    "TranscriptSourcePort",
    "TranscriptionRequest",
    "TranscriptionResult",
    "WordOffset",
]
