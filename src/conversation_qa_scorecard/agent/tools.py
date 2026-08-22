"""Tool functions an agent runtime calls: thin, side-effect-honest wrappers on the services.

Design rules, in the order they matter:

* **No business logic here.** The domain service decides HOW; the model only decides WHICH tool
  to call. A rule that lives in a tool wrapper is a rule the CLI and the API do not have.
* **Rule R8 applies on this path too.** A failing scorecard is ROUTED from inside the domain
  service, in the same call that produced it. An agent surface that only returned the flag
  would be a third place an escalation can quietly stop, after the API and the CLI.
* **Import-safe without a runtime.** ``google.adk`` is imported lazily inside
  :func:`build_function_tools`, so these callables are importable, testable and runnable with
  no ADK and no cloud SDK installed.
* **Typed and documented.** A runtime derives each tool's name, description and JSON parameter
  schema from the signature and the docstring, so both are part of the contract.

One thing this surface does that the API does not have to: it redacts the tool RESULT again.
The transcript was masked at ingestion, so the scorecard already carries no identifier, but a
tool result goes into a model's context and P-04 says minimise what reaches a model. Walking
the whole structure rather than three named fields means a field added later cannot arrive
unmasked because nobody remembered it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hex_service_kit.serialization import to_jsonable
from pii_kit import redact

from ..config import Container, Settings, build_container
from ..contacts import contact_catalogue, find_contact
from ..domain.kernel import utcnow
from ..domain.pii import PII_PATTERNS
from ..service import build_service, pack_for_contact

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from google.adk.tools import FunctionTool

#: The identity a tool call is attributed to when the runtime propagates none. It names the
#: SERVICE, not a person, so an unattributed action is never mistaken for a human's.
DEFAULT_ACTOR = "conversation-qa-scorecard-agent"


def _container(settings: Settings | None) -> Container:
    return build_container(settings)


def _redacted(node: Any) -> Any:
    """Mask personal data in every string of a tool result, however deeply it is nested."""
    if isinstance(node, str):
        return redact(node, PII_PATTERNS)
    if isinstance(node, dict):
        return {key: _redacted(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_redacted(value) for value in node]
    return node


def score_contact(
    contact_id: str,
    tenant: str,
    actor: str = DEFAULT_ACTOR,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Score one contact against its market's score pack and route it for review if it fails.

    Produces a deterministic compliance and quality scorecard: which mandated disclosures were
    made, in what order, inside which timing window, which deterministic sentiment and
    vulnerability cues fired, and the overall disposition. Every number and the disposition
    come from pure code; the narration only restates them. Writes an already-redacted audit
    event and, when the scorecard does not pass, submits it to the human-review console.

    Args:
      contact_id: The contact to score, as the contact catalogue knows it.
      tenant: The tenant partition the caller is authorised for. A contact belonging to any
        other tenant is refused.
      actor: The verified identity this call is attributed to.

    Returns:
      A JSON-safe scorecard with every string masked for personal data (P-04: a tool result
      goes into a model's context), plus ``review_ref``: where the escalation WENT. It is
      empty only when the scorecard passed, so a caller can tell a routed escalation from a
      flag nobody read.
    """
    container = _container(settings)
    contact = find_contact(container, contact_id)
    scorecard = build_service(container).score_contact(
        contact,
        pack_for_contact(container, contact),
        actor=actor,
        tenant=tenant,
        as_of=utcnow(),
    )
    payload = _redacted(to_jsonable(scorecard))
    if not isinstance(payload, dict):  # pragma: no cover - dataclasses serialise to objects
        raise TypeError("a scorecard must serialise to a JSON object")
    return payload


def list_contacts(tenant: str, settings: Settings | None = None) -> dict[str, Any]:
    """List the contacts this tenant may score, with the obligations each one carries.

    Args:
      tenant: The tenant partition the caller is authorised for. Contacts belonging to any
        other tenant are not listed.

    Returns:
      A dict with ``contacts``: one entry per contact, carrying its market, its product and
      the requirement ids it declares. An obligation listed here that the active pack does not
      configure scores as a GAP, never as a pass.
    """
    container = _container(settings)
    return {
        "contacts": [
            {
                "contact_id": contact.contact_id,
                "market": contact.market.value,
                "product": contact.product,
                "declared_requirement_ids": list(contact.declared_requirement_ids),
            }
            for contact in contact_catalogue(container)
            if contact.tenant == tenant
        ]
    }


def verify_audit_trail(settings: Settings | None = None) -> dict[str, Any]:
    """Verify the audit trail's hash chain and its external head anchor.

    Returns:
      A dict with ``ok``, the record counts and a ``detail`` string. ``ok`` is false for an
      edited, deleted or reordered record, and, when an external anchor is configured, for a
      truncated tail as well. Without an anchor a truncation cannot be detected, and the detail
      says so rather than implying a stronger guarantee than the store provides.
    """
    resolved = settings or Settings.load()
    audit = _container(resolved).audit
    verify = getattr(audit, "verify", None)
    if verify is None:
        raise NotImplementedError(
            f"the {resolved.profile} audit adapter does not expose chain verification; a "
            "managed WORM sink is verified by its own retention policy, not from here"
        )
    report = verify()
    return {
        "ok": report.ok,
        "entries": report.entries,
        "chained": report.chained,
        "legacy": report.legacy,
        "first_bad_seq": report.first_bad_seq,
        "detail": report.detail,
        "anchored": bool(resolved.audit_anchor_path),
    }


#: The tool table. The agent card advertises exactly these, by function name.
TOOL_FUNCTIONS = (score_contact, list_contacts, verify_audit_trail)


def build_function_tools() -> list[FunctionTool]:
    """Wrap each callable as a runtime FunctionTool (the only ADK-dependent code path).

    The import is deliberately here rather than at module scope: without it this module, the
    card and every tool would need an agent runtime installed to be imported at all, and the
    offline gate installs none.
    """
    # No ignore comment: the missing-import error for this module is already reported (and
    # ignored) at the TYPE_CHECKING import above, and a second one would be flagged as unused.
    from google.adk.tools import FunctionTool

    return [FunctionTool(func=function) for function in TOOL_FUNCTIONS]
