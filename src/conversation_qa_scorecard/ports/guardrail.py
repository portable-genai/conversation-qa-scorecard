"""GuardrailPort: screens both generation calls this service makes, in both directions (rule R1).

This service makes two model calls, both advisory rather than consequential
(``domain/narration.py``, ``domain/scorecard_service.py``): the narrator restates an
already-decided scorecard, and the signal classifier adds one advisory sentence. Neither
produces the verdict. A :class:`GuardrailPort` screens the PROMPT each call sends, exactly as
sent (:func:`~.narration.narration_prompt`, :func:`~.signals.signal_prompt`), BEFORE the call,
and the text that comes back AFTER it and before it is attached, audited or returned. The screen
lives in the domain (``ScorecardService._narrate`` / ``ScorecardService._advisory``), never in an
adapter, because it wraps the STEP and must stay in place whatever produces the text.

A refused direction is audited ``Decision.BLOCKED`` and the model's text is never used: the
narrator falls back to :func:`~..domain.narration.deterministic_narration` (fixed text built from
the engine's figures) and the classifier contributes no advisory note. The consequential
scorecard was complete before either call ran, so that fallback is the whole answer, never a
partial one.

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
        """Screen inbound prompt or outbound response text; may sanitise it.

        Never raises on a policy match: a block is reported as ``GuardrailVerdict(allowed=False,
        ...)`` so the caller can audit the attempt before falling back. An allowed verdict
        carries ``sanitized_text``, the text the caller uses from then on EXACTLY as given (the
        input unchanged when nothing was redacted, possibly empty when everything was); the
        caller never falls back to the unscreened original.

        Raising is reserved for the adapter being unable to decide at all: its backend errored
        or timed out, or the on-prem placeholder is bound. The domain treats every such raise as
        a refusal (fail closed): it audits it BLOCKED and does not use the model's text.
        """
        ...
