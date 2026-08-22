"""On-prem SignalClassifierPort: fail-fast portability placeholder (the exit proof, P-12)."""

from __future__ import annotations

from ...config import Settings
from ...domain.models import AdvisoryNote
from ...ports.signals import SignalRequest


class OnPremSignalClassifier:
    """Satisfies SignalClassifierPort but refuses: the client wires its own model gateway.

    The deterministic cue lexicons in the score pack are unaffected: sentiment and
    vulnerability findings are produced by the engine and are already on the scorecard before
    this port is called, so an on-premises deployment loses the advisory sentence and no
    finding at all.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def classify(self, request: SignalRequest) -> tuple[AdvisoryNote, ...]:
        raise NotImplementedError(
            "on-prem advisory classification is a portability placeholder: bind the client's "
            "own model gateway (see docs/onprem-migration.md). The deterministic cue findings "
            "do not depend on it."
        )
