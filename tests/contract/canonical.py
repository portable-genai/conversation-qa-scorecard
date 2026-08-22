"""ONE canonical request per port, shared by the structural and behavioural contract suites.

Parity means the same request through every implementation, so the request needs a single home.
Retyping it per suite is how two "parity" tests end up asserting different things.

Each :class:`PortCase` answers three questions about one port:

* ``invoke``   : what a single canonical call to this port looks like;
* ``answered`` : what it means for the OFFLINE family to have actually answered (a port that
  returns ``None`` and records nothing has not answered, it has merely not raised);
* ``managed_refusal`` : what the MANAGED family must do when called with no cloud reachable.
  Never a silent success: either it refuses because it is unconfigured, or its lazy SDK import
  fails. Both are honest; returning as if the work happened is not.

The canonical scorecard is not hand-written. It is produced by running the REAL pipeline
(shipped pack, shipped fixture, redaction, engine) over the canonical contact, so a change that
alters a verdict changes what every parity suite carries rather than leaving a stale literal
that agrees with nothing.

Adding a port means adding a case here. ``test_port_parity.py`` fails the build if this table
and the port map ever disagree, so the touch list in ``CONTRIBUTING.md`` is enforced rather than
merely written down.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent_eval_kit import EvalReport
from hex_service_kit.identity import IdentityError, Principal, RequestContext
from hex_service_kit.observability import TokenUsage

from conversation_qa_scorecard.adapters.local.transcription import (
    FixtureTranscriptSource,
)
from conversation_qa_scorecard.config import (
    Settings,
)
from conversation_qa_scorecard.domain.ingestion import (
    redact_for_scoring,
)
from conversation_qa_scorecard.domain.kernel import (
    AuditEvent,
    Citation,
    Decision,
    Severity,
)
from conversation_qa_scorecard.domain.models import (
    ContactRecord,
    ScoringRequest,
)
from conversation_qa_scorecard.domain.narration import (
    narration_brief,
)
from conversation_qa_scorecard.domain.scoring_engine import (
    ScoringEngine,
)
from conversation_qa_scorecard.domain.serialization import (
    scorecard_to_row,
)
from conversation_qa_scorecard.ports.signals import (
    SignalRequest,
)
from conversation_qa_scorecard.score_pack import (
    load_packs,
)

from tests.fixtures import sample_cases

#: The moment every canonical assessment is made at. Explicit, because the engine takes
#: ``as_of`` as a parameter and a replayable fixture must not depend on today's date.
AS_OF = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)

_SETTINGS = Settings(profile="local", audit_path=":memory:", scorecard_path=":memory:")
_SOURCE = FixtureTranscriptSource(_SETTINGS)
_PACK = load_packs()[sample_cases.SG_PACK_ID]


def _contact(contact_id: str) -> ContactRecord:
    for record in _SOURCE.contacts():
        if record.contact_id == contact_id:
            return record
    raise AssertionError(f"the shipped fixture set has no contact {contact_id!r}")


CANONICAL_CONTACT = _contact(sample_cases.BREACH_CONTACT)
CANONICAL_TRANSCRIPT = _SOURCE.fetch(
    CANONICAL_CONTACT.contact_id,
    locale=CANONICAL_CONTACT.locale,
    audio_uri=CANONICAL_CONTACT.audio_uri,
)

_REDACTED = redact_for_scoring(CANONICAL_TRANSCRIPT)

#: The failing scorecard every review-router implementation is handed (rule R8's payload), and
#: every store implementation persists. Produced by the real engine, never hand-written.
CANONICAL_SCORECARD = ScoringEngine().score(
    ScoringRequest(
        contact=CANONICAL_CONTACT,
        pack=_PACK,
        as_of=AS_OF,
        redaction_count=_REDACTED.count,
    ),
    _REDACTED.transcript,
)

#: The flat projection every warehouse implementation is handed.
CANONICAL_ROW = scorecard_to_row(CANONICAL_SCORECARD)

#: The brief every narrator implementation is handed. Already decided, already redacted.
CANONICAL_BRIEF = narration_brief(CANONICAL_SCORECARD)

#: The advisory request every classifier implementation is handed: customer turns only.
CANONICAL_SIGNAL_REQUEST = SignalRequest(
    contact_id=CANONICAL_CONTACT.contact_id,
    locale=_PACK.locale,
    customer_utterances=tuple(
        turn.text for turn in _REDACTED.transcript.turns if turn.role.value == "customer"
    ),
    detected_cue_ids=tuple(s.cue_id for s in CANONICAL_SCORECARD.signals if s.detected),
)

#: The audit record every audit-port implementation is handed. Already redacted, as the port
#: requires: a raw identifier must never reach a WORM record.
CANONICAL_EVENT = AuditEvent(
    action="score_contact",
    actor=sample_cases.ACTOR,
    decision=Decision.ESCALATED,
    severity=Severity.CRITICAL,
    redacted_summary=f"{CANONICAL_CONTACT.contact_id}: non_compliant on SG",
    citations=(Citation(source_id="sg_mas_fair_dealing", title="Fair dealing", snippet="risk"),),
)

#: The inbound transport context every identity implementation is handed.
CANONICAL_CONTEXT = RequestContext(headers={"x-dev-persona": "auditor"})


@dataclass(frozen=True, slots=True)
class PortCase:
    """One port's canonical call plus the two verdicts the parity suites need."""

    invoke: Callable[[Any], Any]
    answered: Callable[[Any, Any], bool]
    managed_refusal: tuple[type[BaseException], ...]
    detail: str


def _audit_invoke(adapter: Any) -> Any:
    return adapter.record(CANONICAL_EVENT)


def _audit_answered(adapter: Any, _result: Any) -> bool:
    stored = adapter.log.read_all()
    return bool(stored) and stored[-1]["actor"] == sample_cases.ACTOR and adapter.verify().ok


def _identity_invoke(adapter: Any) -> Any:
    return adapter.resolve(CANONICAL_CONTEXT)


def _identity_answered(_adapter: Any, result: Any) -> bool:
    return isinstance(result, Principal) and bool(result.actor)


def _review_invoke(adapter: Any) -> Any:
    return adapter.route(CANONICAL_SCORECARD, maker=sample_cases.ACTOR, tenant=sample_cases.TENANT)


def _review_answered(adapter: Any, result: Any) -> bool:
    return bool(result) and len(adapter.outbox.pending()) == 1


def _transcription_invoke(adapter: Any) -> Any:
    return adapter.fetch(
        CANONICAL_CONTACT.contact_id,
        locale=CANONICAL_CONTACT.locale,
        audio_uri=CANONICAL_CONTACT.audio_uri,
    )


def _transcription_answered(_adapter: Any, result: Any) -> bool:
    return bool(getattr(result, "turns", ())) and bool(getattr(result, "locale", ""))


def _store_invoke(adapter: Any) -> Any:
    return adapter.put(CANONICAL_SCORECARD)


def _store_answered(adapter: Any, result: Any) -> bool:
    stored = adapter.get(CANONICAL_SCORECARD.scorecard_id)
    listed = adapter.list_for_contact(CANONICAL_SCORECARD.tenant, CANONICAL_SCORECARD.contact_id)
    return (
        result == CANONICAL_SCORECARD.scorecard_id
        and stored is not None
        and stored.disposition is CANONICAL_SCORECARD.disposition
        and len(listed) == 1
    )


def _narration_invoke(adapter: Any) -> Any:
    return adapter.narrate(CANONICAL_BRIEF)


def _narration_answered(_adapter: Any, result: Any) -> bool:
    return result is not None and bool(result.body)


def _signals_invoke(adapter: Any) -> Any:
    return adapter.classify(CANONICAL_SIGNAL_REQUEST)


def _signals_answered(_adapter: Any, result: Any) -> bool:
    return bool(result) and all(note.text for note in result)


def _warehouse_invoke(adapter: Any) -> Any:
    return adapter.export([CANONICAL_ROW])


def _warehouse_answered(adapter: Any, result: Any) -> bool:
    return result == 1 and len(adapter.rows) == 1


def _tracer_invoke(adapter: Any) -> Any:
    with adapter.span("canonical.unit", action="canonical"):
        adapter.record_token_usage(TokenUsage(input_tokens=7, output_tokens=2), "canonical-model")
    return True


def _tracer_answered(adapter: Any, result: Any) -> bool:
    return bool(result)


def _evaluation_invoke(adapter: Any) -> Any:
    return adapter.evaluate("eval/datasets/canonical.jsonl")


def _evaluation_answered(adapter: Any, result: Any) -> bool:
    return isinstance(result, EvalReport) and result.dataset.endswith("canonical.jsonl")


CANONICAL_CALLS: dict[str, PortCase] = {
    "audit": PortCase(
        invoke=_audit_invoke,
        answered=_audit_answered,
        # The lazy `google.cloud` import is the first thing the managed sink does.
        managed_refusal=(ImportError,),
        detail="write one already-redacted WORM record",
    ),
    "identity": PortCase(
        invoke=_identity_invoke,
        answered=_identity_answered,
        # No IAP assertion header offline, so the managed adapter refuses before importing.
        managed_refusal=(IdentityError,),
        detail="resolve a verified principal from transport context",
    ),
    "narration": PortCase(
        invoke=_narration_invoke,
        answered=_narration_answered,
        # The lazy `google.genai` import; an unconfigured model refuses first with RuntimeError.
        managed_refusal=(ImportError, RuntimeError),
        detail="draft a grounded narrative restating the decided scorecard",
    ),
    "review_router": PortCase(
        invoke=_review_invoke,
        answered=_review_answered,
        # Rule R8: with no console configured the managed router must refuse, not swallow.
        managed_refusal=(RuntimeError,),
        detail="route one failing scorecard to human review",
    ),
    "scorecard_store": PortCase(
        invoke=_store_invoke,
        answered=_store_answered,
        managed_refusal=(ImportError,),
        detail="persist and read back one tenant-scoped scorecard",
    ),
    "signal_classifier": PortCase(
        invoke=_signals_invoke,
        answered=_signals_answered,
        managed_refusal=(ImportError, RuntimeError),
        detail="return advisory colour on already-decided signals",
    ),
    "transcription": PortCase(
        invoke=_transcription_invoke,
        answered=_transcription_answered,
        managed_refusal=(ImportError,),
        detail="produce an assembled transcript for the contact",
    ),
    "warehouse": PortCase(
        invoke=_warehouse_invoke,
        answered=_warehouse_answered,
        # An unconfigured destination refuses rather than accepting rows that go nowhere.
        managed_refusal=(RuntimeError,),
        detail="accept one flat scorecard row for the warehouse",
    ),
    "tracer": PortCase(
        invoke=_tracer_invoke,
        answered=_tracer_answered,
        # NOTHING. Tracing is not essential to correctness, so the managed adapter must not refuse
        # offline either: with no SDK installed it degrades to a no-op and the traced body still
        # runs. An adapter that raised here would take a request down over a diagnostic.
        managed_refusal=(),
        detail="open one span and report the cost of a model call",
    ),
    "evaluation": PortCase(
        invoke=_evaluation_invoke,
        answered=_evaluation_answered,
        # The managed gate reaches Hrz4 over HTTP, which is unreachable offline.
        managed_refusal=(Exception,),
        detail="score one golden dataset through the promotion authority",
    ),
}
