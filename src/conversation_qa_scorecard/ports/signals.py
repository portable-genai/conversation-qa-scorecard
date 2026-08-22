"""SignalClassifierPort: advisory colour on sentiment and vulnerability. Consequential: no.

The scorecard's sentiment and vulnerability findings come from DETERMINISTIC cue lexicons in
the score pack. This port exists so a model classifier can add a sentence of colour next to
them ("the customer sounded rushed throughout"), and for nothing else.

What "advisory only" means here, concretely, and how it is enforced:

* the classifier is called AFTER the engine has produced every finding, every score and the
  disposition, and its output is attached to a field the engine never reads
  (``Scorecard.advisory``);
* ``tests/unit/test_model_free_scorecard.py`` scores the same contact twice, once with the
  bound classifier and narrator and once with both stubbed out, and asserts every other field
  is byte-identical. A classifier that started moving a number would fail that test rather
  than quietly changing a QA outcome;
* an :class:`AdvisoryNote` carries no severity, no polarity and no requirement id, so there is
  nothing in its shape for a future caller to promote into a decision by accident.

Returning an empty tuple is a first-class answer. So is raising: the caller treats any failure
as "no advisory colour" and the scorecard is unchanged, because the consequential part of the
answer was complete before the call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..domain.models import AdvisoryNote


@dataclass(frozen=True, slots=True)
class SignalRequest:
    """The already-redacted material a classifier may see, and nothing else.

    ``customer_utterances`` are redacted customer turns only. Agent turns are deliberately
    withheld: a scripted empathy line is not evidence about the customer, and a classifier
    that saw both would attribute one to the other.
    """

    contact_id: str
    locale: str
    customer_utterances: tuple[str, ...]
    detected_cue_ids: tuple[str, ...] = ()


@runtime_checkable
class SignalClassifierPort(Protocol):
    def classify(self, request: SignalRequest) -> tuple[AdvisoryNote, ...]:
        """Return advisory notes for ``request``, or an empty tuple. Never a verdict."""
        ...
