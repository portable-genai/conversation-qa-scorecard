"""Resolving a contact id into the record this service scores, and saying so when it cannot.

A contact record (who owns it, which market and product it was routed under, and which
obligations it therefore carries) comes from the contact-centre platform. That integration is
NOT built here, and this module is where that is stated rather than hidden.

* Under the offline profile the bound transcript source ships a synthetic contact catalogue,
  so the demo, the eval, the tests and the UI's picker all have real records to work with.
* Under a managed or on-premises profile there is no catalogue, and :func:`find_contact` raises
  :class:`ContactCatalogueUnavailableError`, which the API turns into a 501 naming the missing
  integration. The alternative (inventing a contact record with an empty obligation list)
  would score every contact as INDETERMINATE and look like a working deployment.

The obligation list is the reason this matters. ``declared_requirement_ids`` is what the
contact OWES, and it is deliberately independent of what the pack CONFIGURES: an obligation
present in one and absent from the other is a ``GAP``, and that comparison is only possible
when the two lists have different sources.
"""

from __future__ import annotations

from .config import Container
from .domain.models import ContactRecord


class ContactCatalogueUnavailableError(NotImplementedError):
    """This profile has no contact catalogue bound, so a contact id cannot be resolved."""

    http_status = 501


class ContactNotFoundError(LookupError):
    """No contact with that id exists in the bound catalogue."""

    http_status = 404


def contact_catalogue(container: Container) -> tuple[ContactRecord, ...]:
    """Every contact the bound transcript source can describe, or raise if it describes none."""
    source = container.transcription
    lister = getattr(source, "contacts", None)
    if lister is None:
        raise ContactCatalogueUnavailableError(
            "the bound transcript source has no contact catalogue. A deployment resolves "
            "contacts from its contact-centre platform; that integration is not built in this "
            "repo, and inventing a contact with no declared obligations would score every "
            "assessment as indeterminate while looking like a working deployment."
        )
    return tuple(lister())


def find_contact(container: Container, contact_id: str) -> ContactRecord:
    """Resolve one contact id, or raise. Never returns a placeholder record."""
    for contact in contact_catalogue(container):
        if contact.contact_id == contact_id:
            return contact
    raise ContactNotFoundError(f"no contact {contact_id!r} in the bound catalogue")


def headline_for(container: Container, contact_id: str) -> str:
    """The one-line description of what a synthetic contact demonstrates (demo and UI only)."""
    describer = getattr(container.transcription, "headline", None)
    return "" if describer is None else str(describer(contact_id))
