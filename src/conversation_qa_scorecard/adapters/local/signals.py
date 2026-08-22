"""Local SignalClassifierPort: the SDK-free advisory classifier. Colour only, never a verdict.

The offline profile has no model, so this produces one plainly-labelled advisory sentence from
what the DETERMINISTIC engine already found. It changes nothing: the notes it returns land in
``Scorecard.advisory``, a field the engine never reads, and
``tests/unit/test_model_free_scorecard.py`` compares a scorecard produced with this adapter
bound against one produced with it stubbed out and requires every other field to be identical.

Its confidence is deliberately reported as 0.0. An offline stand-in has no evidence for a
confidence figure, and inventing one would put a number nobody computed onto a compliance
artifact, which is the exact failure mode the determinism rule exists to prevent.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import AdvisoryNote, SignalKind
from ...ports.signals import SignalRequest

_SOURCE = "offline-advisory"


class LocalSignalClassifier:
    """Restate the deterministic cue findings as advisory colour, offline."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def classify(self, request: SignalRequest) -> tuple[AdvisoryNote, ...]:
        if not request.customer_utterances:
            return ()
        turns = len(request.customer_utterances)
        if request.detected_cue_ids:
            text = (
                "Advisory only: the deterministic cue lexicon matched "
                + ", ".join(sorted(request.detected_cue_ids))
                + f" across {turns} customer turns. This note changes no score and no verdict."
            )
        else:
            text = (
                f"Advisory only: no cue in the configured lexicon matched across {turns} "
                "customer turns. This note changes no score and no verdict."
            )
        return (
            AdvisoryNote(
                source=_SOURCE,
                kind=SignalKind.SENTIMENT,
                text=text,
                confidence=0.0,
            ),
        )
