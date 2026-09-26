"""GuardrailPort: screens the narration and signal-classifier generation calls (rule R1).

This service makes two model-shaped calls, both advisory rather than consequential
(``domain/narration.py``, ``domain/scorecard_service.py``): the narrator restates an
already-decided scorecard, and the signal classifier adds one advisory sentence. Neither
produces the verdict. A :class:`GuardrailPort` screens the text going INTO each call and the
text coming OUT of it, in the domain (``ScorecardService._narrate`` /
``ScorecardService._advisory``), never in an adapter, because the screen wraps the STEP and
must stay in place even after a narrator starts drafting a real sentence instead of restating
figures.

A blocked direction is never surfaced as a service failure: the consequential scorecard was
already complete before either call ran (``domain/scorecard_service.py`` docstring, rule 3), so
a block degrades the SAME way an unreachable model already does -- the narrator falls back to
:func:`~..domain.narration.deterministic_narration` and the classifier contributes no advisory
colour -- logged, never raised.

The three adapters are the usual shape: ``local`` is a deterministic heuristic screen (there is
no offline Model Armor emulator), ``gcp`` calls a regional Model Armor template, and ``onprem``
is a fail-fast portability placeholder.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.kernel import Direction, GuardrailVerdict


@runtime_checkable
class GuardrailPort(Protocol):
    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        """Screen ``text`` for ``direction`` (INPUT before a call runs, OUTPUT after).

        Returning a verdict with ``allowed=False`` is a first-class answer, not an error. The
        on-premises placeholder is the one adapter that may raise (a portability refusal); the
        other two always return a :class:`~..domain.kernel.GuardrailVerdict`.
        """
        ...
