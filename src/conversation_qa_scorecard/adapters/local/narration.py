"""Local NarrationPort: the SDK-free narrator. Deterministic, and deliberately unimpressive.

The offline profile has no model, so this composes the same sentences the domain's fallback
would, from the brief and nothing else. It exists so the narration SEAM is exercised in the
gate, the demo and the eval rather than being a code path nobody runs until production: the
grounding validator, the fallback and the "identical with the model stubbed out" check all run
against a real bound adapter here.

Everything it writes is drawn from ``brief.allowed_figures``, so it passes the grounding gate
by construction. ``tests/unit/test_narration_grounding.py`` asserts that, which makes this
adapter a standing proof that the gate is not so strict it rejects a correct narration.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import Narration
from ...ports.narration import NarrationBrief

_MODEL = "offline-deterministic"


class LocalNarrator:
    """Compose a grounded narrative offline, with no model and no network."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def narrate(self, brief: NarrationBrief) -> Narration | None:
        verdict = brief.disposition.replace("_", " ")
        sentences = [
            f"Contact {brief.contact_id} in market {brief.market} is {verdict} "
            f"at severity {brief.severity}."
        ]
        if brief.failing_requirements:
            listed = ", ".join(
                f"{requirement_id} ({status})"
                for requirement_id, status, _severity in brief.failing_requirements
            )
            sentences.append(f"Unmet obligations: {listed}.")
        else:
            sentences.append("No declared obligation was left unmet.")
        if brief.detected_cues:
            cues = ", ".join(sorted(cue_id for cue_id, _kind in brief.detected_cues))
            sentences.append(f"Deterministic cues detected: {cues}.")
        sentences.append(
            f"Disclosure coverage {brief.disclosure_score:.2f}, "
            f"script adherence {brief.adherence_score:.2f}."
        )
        return Narration(
            headline=f"{verdict}: {brief.contact_id}",
            body=" ".join(sentences),
            citations=(),
            model=_MODEL,
            grounded=True,
        )
