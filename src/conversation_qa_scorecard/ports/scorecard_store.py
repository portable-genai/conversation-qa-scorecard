"""ScorecardStorePort: the tenant-scoped store of produced scorecards.

A QA scorecard is tenant-owned data with a regulator's interest attached, so the two read
methods differ DELIBERATELY:

* :meth:`list_for_contact` takes the tenant and MUST filter on it in the store, so a query can
  never span tenants even when a caller passes another tenant's contact id, and
* :meth:`get` is a raw fetch by id that does NOT filter: the caller (the domain's scorecard
  service) compares the record's tenant to the VERIFIED principal's tenant and denies with
  ``TenantAccessDeniedError``, which every surface maps to HTTP 403.

Keeping the comparison in the DOMAIN, not in the adapter, is what makes it true on every
surface at once: the API, the CLI and the agent tools all go through the same service, and an
adapter cannot become the only place the boundary is enforced. It is also why the split is
worth the asymmetry: the store hands the record over and the domain refuses to serve it, so
the cross-tenant denial test is testing the rule rather than testing a query.

403 rather than 404, deliberately. The record EXISTS and this caller may not have it. Answering
404 makes the store's contents probeable by anyone with an id generator, and tells the operator
reading the log the wrong story about what went wrong.

Never pass a client-supplied tenant into either method: the tenant comes from the ``Principal``
the identity adapter verified.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import Scorecard


@runtime_checkable
class ScorecardStorePort(Protocol):
    def list_for_contact(self, tenant: str, contact_id: str) -> tuple[Scorecard, ...]:
        """Scorecards ``tenant`` holds for ``contact_id`` (store-side tenant filter)."""
        ...

    def get(self, scorecard_id: str) -> Scorecard | None:
        """Return one scorecard by id, or ``None``; the DOMAIN authorizes the tenant."""
        ...

    def put(self, scorecard: Scorecard) -> str:
        """Upsert one scorecard (the id is deterministic, so a re-score updates in place)."""
        ...
