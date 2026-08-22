"""Managed NarrationPort: Gemini restating an already-decided scorecard. Nothing more.

The prompt below is the whole of the model's instruction set, and it is short on purpose. The
model is handed the decided figures and told to restate them; it is not asked to assess, to
weigh, to conclude or to estimate, because every one of those has already happened in pure
code before this adapter is reached.

The draft is not trusted on the way back either. ``domain/narration.py`` validates it against
the exact figure set carried in the brief and discards it whole if it mentions a number the
engine did not publish or cites an instrument the findings did not. Prompting and validating
are two different controls and this service uses both, because a prompt is a request and a
validator is a rule.

The SDK import is lazy, so the offline profiles import this module with none installed. When
the model is unconfigured or the SDK is absent this adapter REFUSES loudly rather than
returning ``None``: a managed adapter that quietly reports "no narration available" is
indistinguishable from one that is working, and the fleet's rule is that a managed family
refuses in a documented way. The caller (``domain/scorecard_service.py``) is the single place
that turns any narrator failure into the deterministic fallback, so a compliance assessment is
still never blocked on a model.
"""

from __future__ import annotations

import json
from typing import Any

from ...config import Settings
from ...domain.models import Narration
from ...ports.narration import NarrationBrief

_SYSTEM_INSTRUCTION = (
    "You write one short paragraph for a contact-centre QA manager. You are given a scorecard "
    "that has ALREADY been decided by deterministic code. Restate it. You must not assess, "
    "weigh, estimate or conclude anything, and you must not mention any number that is not in "
    "the allowed_figures list you are given. Do not cite any source that is not in "
    'citation_ids. Reply with JSON: {"headline": str, "body": str}.'
)


class GeminiNarrator:
    """Draft a grounded narrative with the managed model, in the residency region."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def narrate(self, brief: NarrationBrief) -> Narration | None:  # pragma: no cover - needs GCP
        model = (self._settings.narration_model or "").strip()
        if not model:
            raise RuntimeError(
                "narration_model is not configured, so the managed narrator has no model to "
                "call. Refusing rather than reporting an absent narration as a working one."
            )
        # Lazy import: absent in the offline profiles and in CI, where the ImportError IS the
        # documented refusal.
        from google import genai

        client = genai.Client(vertexai=True, location=self._settings.region)
        reply = client.models.generate_content(
            model=model,
            contents=json.dumps(_payload(brief), sort_keys=True),
            config={
                "system_instruction": _SYSTEM_INSTRUCTION,
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        )
        parsed = _parse(getattr(reply, "text", "") or "")
        if parsed is None:
            return None
        return Narration(
            headline=str(parsed.get("headline", "")),
            body=str(parsed.get("body", "")),
            citations=(),
            model=model,
            grounded=False,
        )


def _payload(brief: NarrationBrief) -> dict[str, Any]:
    """Exactly what the model sees: decided figures, already redacted, and nothing else."""
    return {
        "contact_id": brief.contact_id,
        "market": brief.market,
        "disposition": brief.disposition,
        "severity": brief.severity,
        "disclosure_score": brief.disclosure_score,
        "adherence_score": brief.adherence_score,
        "sentiment_score": brief.sentiment_score,
        "failing_requirements": [list(item) for item in brief.failing_requirements],
        "detected_cues": [list(item) for item in brief.detected_cues],
        "citation_ids": list(brief.citation_ids),
        "allowed_figures": sorted(brief.allowed_figures),
    }


def _parse(text: str) -> dict[str, Any] | None:  # pragma: no cover - needs live GCP
    """Parse the reply, returning ``None`` on anything that is not the declared object."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
