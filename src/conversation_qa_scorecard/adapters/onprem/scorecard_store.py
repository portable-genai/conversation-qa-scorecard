"""On-prem ScorecardStorePort: fail-fast portability placeholder (the exit proof, P-12)."""

from __future__ import annotations

from ...config import Settings
from ...domain.models import Scorecard


class OnPremScorecardStore:
    """Satisfies ScorecardStorePort but refuses: the client wires its own record store.

    Every method raises, including the reads. A store that answered an empty tuple would make
    a tenant's whole scorecard history look like a tenant with no findings, which is the most
    dangerous empty answer this service could give.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def list_for_contact(self, tenant: str, contact_id: str) -> tuple[Scorecard, ...]:
        raise NotImplementedError(self._message())

    def get(self, scorecard_id: str) -> Scorecard | None:
        raise NotImplementedError(self._message())

    def put(self, scorecard: Scorecard) -> str:
        raise NotImplementedError(self._message())

    @staticmethod
    def _message() -> str:
        return (
            "on-prem scorecard store is a portability placeholder: bind the client's own "
            "record store (see docs/onprem-migration.md). The tenant comparison stays in the "
            "domain service whichever store is bound."
        )
