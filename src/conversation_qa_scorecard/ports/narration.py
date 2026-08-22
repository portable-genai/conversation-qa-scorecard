"""NarrationPort: the model boundary, and the narrowest one in this service.

A narrator receives a :class:`NarrationBrief` that has ALREADY been decided: the disposition,
the scores and every finding are settled facts by the time this port is called. The model's
only job is to restate them in a paragraph a QA manager can read.

Three constraints, and each of them is enforced elsewhere rather than trusted here:

* **It may only restate.** The brief carries the exact figures the engine published, and the
  domain validates the returned draft against them (``domain/narration.py``). A draft that
  mentions a number the engine did not publish, or cites an instrument the engine did not
  emit, is DISCARDED and the deterministic summary stands.
* **It sees redacted text only.** The brief is built from an already-redacted transcript and
  already-redacted evidence spans. No raw identifier reaches a model, ever.
* **It is optional.** Returning ``None`` is a first-class answer, and so is RAISING. A managed
  adapter with no model configured and no SDK present refuses loudly rather than reporting an
  absent narration as a working one, and ``domain/scorecard_service.py`` is the single place
  that turns any failure into the deterministic fallback. A scorecard is therefore never
  blocked on a model being reachable, and a broken managed binding is never invisible.

The adapters are the usual three: an offline deterministic drafter that composes a sentence
from the brief with no SDK, the managed Gemini adapter with a lazy import, and the on-premises
placeholder that refuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..domain.models import Narration


@dataclass(frozen=True, slots=True)
class NarrationBrief:
    """Everything a narrator is allowed to see. Already decided, already redacted.

    ``allowed_figures`` is the closed set of numeric strings the draft may contain. It is
    carried in the brief rather than inferred afterwards so the prompt and the validator agree
    by construction: a narrator that invents "97% adherence" is rejected because 97 is not in
    this set, not because a regexp guessed at what a figure looks like.
    """

    scorecard_id: str
    contact_id: str
    market: str
    disposition: str
    severity: str
    disclosure_score: float
    adherence_score: float
    sentiment_score: int
    failing_requirements: tuple[tuple[str, str, str], ...] = ()
    detected_cues: tuple[tuple[str, str], ...] = ()
    citation_ids: tuple[str, ...] = ()
    allowed_figures: frozenset[str] = field(default_factory=frozenset)
    locale: str = ""


@runtime_checkable
class NarrationPort(Protocol):
    def narrate(self, brief: NarrationBrief) -> Narration | None:
        """Draft a narrative restating ``brief``, or return ``None`` to decline.

        Never raises for a routine unavailability: an unconfigured or unreachable model is a
        ``None``, and the caller falls back to the deterministic summary.
        """
        ...
