"""On-prem NarrationPort: fail-fast portability placeholder (the exit proof, P-12)."""

from __future__ import annotations

from ...config import Settings
from ...domain.models import Narration
from ...ports.narration import NarrationBrief


class OnPremNarrator:
    """Satisfies NarrationPort but refuses: the client wires its own model gateway.

    Refusing rather than returning ``None`` on purpose. ``None`` is the answer a WORKING
    narrator gives when it declines, so a placeholder returning it would be indistinguishable
    from a healthy binding, and the portability tour would report a seam as implemented.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def narrate(self, brief: NarrationBrief) -> Narration | None:
        raise NotImplementedError(
            "on-prem narration is a portability placeholder: bind the client's own model "
            "gateway (see docs/onprem-migration.md). The scorecard is complete without it."
        )
