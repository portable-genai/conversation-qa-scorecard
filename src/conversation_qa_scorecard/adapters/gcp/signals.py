"""Managed SignalClassifierPort: Gemini adding advisory colour, and never a verdict.

The classifier sees redacted CUSTOMER utterances only and is asked for one advisory sentence.
It is not asked whether the customer is vulnerable: that question is answered by the
deterministic cue lexicon in the score pack, before this adapter is reached, and the answer is
already on the scorecard by the time this runs.

Whatever comes back is wrapped in :class:`AdvisoryNote`, which has no severity, no polarity and
no requirement id, so there is nothing in its shape for a future caller to promote into a
decision by accident. ``tests/unit/test_model_free_scorecard.py`` is the standing proof: the
same contact scored with and without this adapter bound must differ in the advisory field and
in nothing else.

The SDK import is lazy and an unconfigured or absent model REFUSES rather than returning an
empty tuple, for the same reason as the narrator: a managed adapter that quietly reports
"nothing to add" is indistinguishable from one that is working. The caller treats any failure
as "no advisory colour", so the scorecard is unaffected either way.
"""

from __future__ import annotations

import json

from ...config import Settings
from ...domain.models import AdvisoryNote, SignalKind
from ...ports.signals import SignalRequest

_SYSTEM_INSTRUCTION = (
    "You add ONE short advisory sentence about the tone of a customer's turns, for a "
    "contact-centre QA manager. You are not deciding anything: vulnerability and sentiment "
    "have already been determined by deterministic rules. Do not state a score, a percentage, "
    'a verdict or a recommendation. Reply with JSON: {"note": str}.'
)


class GeminiSignalClassifier:
    """Ask the managed model for advisory colour on already-decided signals."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def classify(
        self, request: SignalRequest
    ) -> tuple[AdvisoryNote, ...]:  # pragma: no cover - needs GCP
        model = (self._settings.narration_model or "").strip()
        if not model:
            raise RuntimeError(
                "narration_model is not configured, so the managed advisory classifier has no "
                "model to call. Refusing rather than reporting silence as a working classifier."
            )
        if not request.customer_utterances:
            return ()
        # Lazy import: absent in the offline profiles and in CI, where the ImportError IS the
        # documented refusal.
        from google import genai

        client = genai.Client(vertexai=True, location=self._settings.region)
        reply = client.models.generate_content(
            model=model,
            contents=json.dumps(
                {
                    "locale": request.locale,
                    "customer_utterances": list(request.customer_utterances),
                },
                sort_keys=True,
            ),
            config={
                "system_instruction": _SYSTEM_INSTRUCTION,
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        )
        note = _note(getattr(reply, "text", "") or "")
        if not note:
            return ()
        return (
            AdvisoryNote(
                source=model,
                kind=SignalKind.SENTIMENT,
                text=note,
                confidence=0.0,
            ),
        )


def _note(text: str) -> str:  # pragma: no cover - needs live GCP
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return ""
    return str(parsed.get("note", "")) if isinstance(parsed, dict) else ""
