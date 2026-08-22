"""A review router that accepts an escalation and does NOTHING. The mutant, on purpose.

It exists so ``review_safety`` can be proved able to go red against the exact defect rule R8
was written for: a producer sets ``requires_human_review``, every other test passes, and the
item never reaches a reviewer because the thing that was supposed to route it silently returns.

It is bound by dotted path through the settings ``adapters:`` block, the same documented
rebinding path a deployment uses, so the mutant is introduced the way a real regression would
be introduced rather than by monkey-patching an internal.

Never bind this outside a not-falsely-green proof.
"""

from __future__ import annotations

from conversation_qa_scorecard.config import Settings
from conversation_qa_scorecard.domain.models import Scorecard


class SilentReviewRouter:
    """Satisfies ReviewRouterPort, routes nothing, and reports no reference."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def route(self, scorecard: Scorecard, *, maker: str, tenant: str = "") -> str:
        return ""
