"""ScorecardService: the one path a contact takes, and the one place the rules are enforced.

Every surface (API, CLI, agent tools, demo, eval) goes through this class, which is why the
rules below are true everywhere rather than true in whichever surface remembered them:

1. **Authorisation is against the VERIFIED principal's tenant, and it answers 403.** A caller
   asking about another tenant's contact is refused before a transcript is fetched, and asking
   for another tenant's scorecard raises ``TenantAccessDeniedError`` rather than 404, because
   the record exists and 404 would make the store probeable.
2. **Redact, then everything else.** The transcript is masked by pure code the moment it
   arrives. The engine matches masked text, so every citation indexes text with the
   identifiers already gone, and nothing downstream needs to remember to scrub again.
3. **The engine decides, then the model decorates.** ``ScoringEngine.score`` runs with no
   advisory notes and no narration and produces the consequential answer. The classifier and
   the narrator run afterwards and are attached to fields the engine never reads. Both are
   best-effort: an unreachable model changes nothing about the verdict.
4. **Rule R8: a failing scorecard is ROUTED, in the same call that produced it.** Setting
   ``requires_human_review`` is not the escalation; routing is. The routed reference is stored
   on the scorecard so a caller can tell a routed escalation from a flag that stopped here.
5. **Redact before the audit write.** The audit summary is built from the engine's figures and
   masked again on the way in, so the immutable record can never carry an identifier.

Nothing here computes a score, a status or a disposition. That is all in ``scoring_engine.py``,
which is pure and takes an explicit ``as_of``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from pii_kit import redact
from speech_lexicon_kit import ChannelRole, Transcript

from ..ports.audit import AuditSinkPort
from ..ports.narration import NarrationPort
from ..ports.observability import ObservabilityTracerPort
from ..ports.review_router import ReviewRouterPort
from ..ports.scorecard_store import ScorecardStorePort
from ..ports.signals import SignalClassifierPort, SignalRequest
from ..ports.speech import TranscriptSourcePort
from ..ports.warehouse import WarehouseExportPort
from .errors import ScorecardNotFoundError, TenantAccessDeniedError
from .ingestion import redact_for_scoring
from .kernel import AuditEvent, Decision
from .models import (
    AdvisoryNote,
    ContactRecord,
    Disposition,
    Narration,
    Scorecard,
    ScorePack,
    ScoringRequest,
)
from .narration import deterministic_narration, grounded_or_fallback, narration_brief
from .pii import PII_PATTERNS
from .scoring_engine import ScoringEngine
from .serialization import scorecard_to_row

#: Cap on how many customer utterances the advisory classifier is shown. A bound rather than a
#: whole transcript, because P-04 says minimise what reaches a model and an advisory sentence
#: does not become more advisory with more input.
_MAX_CLASSIFIER_UTTERANCES = 40

#: One span per scored contact. Structural attributes only: see :meth:`score_contact`.
_SCORE_SPAN = "scorecard.score_contact"


class ScorecardService:
    """Score one contact end to end, with the tenant boundary and rule R8 enforced here."""

    def __init__(
        self,
        *,
        audit: AuditSinkPort,
        transcripts: TranscriptSourcePort,
        store: ScorecardStorePort,
        review_router: ReviewRouterPort,
        tracer: ObservabilityTracerPort,
        narrator: NarrationPort | None = None,
        classifier: SignalClassifierPort | None = None,
        warehouse: WarehouseExportPort | None = None,
        engine: ScoringEngine | None = None,
    ) -> None:
        self._audit = audit
        self._transcripts = transcripts
        self._store = store
        self._review_router = review_router
        self._tracer = tracer
        self._narrator = narrator
        self._classifier = classifier
        self._warehouse = warehouse
        self._engine = engine or ScoringEngine()

    # ------------------------------------------------------------------ #
    # The scoring path
    # ------------------------------------------------------------------ #
    def score_contact(
        self,
        contact: ContactRecord,
        pack: ScorePack,
        *,
        actor: str,
        tenant: str,
        as_of: datetime,
        export: bool = True,
    ) -> Scorecard:
        """Score ``contact`` and return the stored scorecard.

        ``tenant`` is the VERIFIED principal's tenant and is checked against the contact's own
        before anything is fetched: a caller must not be able to make this service transcribe
        another tenant's recording, which is a data-access decision as much as a read.

        The whole path runs inside one span. Its attributes are STRUCTURAL only, never the
        transcript, the contact's identifiers or any finding text: a trace backend is not the
        WORM audit trail and has no redaction stage, so anything content-shaped that reaches
        it has left the boundary that `redact_for_scoring` exists to hold.
        """
        with self._tracer.span(
            _SCORE_SPAN,
            action="score_contact",
            actor=actor,
            tenant=tenant,
            market=pack.market,
        ):
            self._authorise(contact.tenant, tenant, contact.contact_id)

            raw = self._transcripts.fetch(
                contact.contact_id,
                locale=contact.locale or pack.locale,
                audio_uri=contact.audio_uri,
            )
            redacted = redact_for_scoring(raw, PII_PATTERNS)

            request = ScoringRequest(
                contact=contact,
                pack=pack,
                as_of=as_of,
                redaction_count=redacted.count,
            )
            # The CONSEQUENTIAL result: no advisory notes, no narration, no model of any kind.
            decided = self._engine.score(request, redacted.transcript)

            advisory = self._advisory(contact, pack, redacted.transcript, decided)
            narration = self._narrate(decided)
            scorecard = replace(decided, advisory=advisory, narration=narration)

            review_ref = ""
            if scorecard.requires_human_review:
                # Rule R8, in the same call that produced the result. A flag nobody routes is
                # auto-execution with extra steps.
                review_ref = self._review_router.route(
                    scorecard, maker=actor, tenant=contact.tenant
                )
                scorecard = replace(scorecard, review_ref=review_ref)

            self._record(scorecard, actor=actor)
            self._store.put(scorecard)
            if export and self._warehouse is not None:
                self._warehouse.export([scorecard_to_row(scorecard)])
            return scorecard

    # ------------------------------------------------------------------ #
    # Reads, authorised in the domain
    # ------------------------------------------------------------------ #
    def fetch(self, scorecard_id: str, *, tenant: str) -> Scorecard:
        """One scorecard by id, authorised against the verified principal's tenant.

        The store's ``get`` is deliberately unfiltered; the comparison is HERE. A record owned
        by another tenant raises ``TenantAccessDeniedError`` (403), and a record that does not
        exist raises ``ScorecardNotFoundError`` (404). Those are different answers on purpose.
        """
        stored = self._store.get(scorecard_id)
        if stored is None:
            raise ScorecardNotFoundError(f"no scorecard {scorecard_id!r} in this deployment")
        self._authorise(stored.tenant, tenant, scorecard_id)
        return stored

    def list_for_contact(self, contact_id: str, *, tenant: str) -> tuple[Scorecard, ...]:
        """Every scorecard this tenant holds for ``contact_id`` (the store filters on tenant)."""
        if not tenant:
            # Fail closed: an unresolved tenant reads nothing rather than everything.
            return ()
        return self._store.list_for_contact(tenant, contact_id)

    @staticmethod
    def _authorise(owner_tenant: str, principal_tenant: str, subject: str) -> None:
        """Refuse unless the verified principal owns the record. Empty never matches empty."""
        if not principal_tenant or not owner_tenant or principal_tenant != owner_tenant:
            raise TenantAccessDeniedError(
                f"{subject!r} belongs to another tenant partition; this principal may not read it"
            )

    # ------------------------------------------------------------------ #
    # The advisory half (never consequential)
    # ------------------------------------------------------------------ #
    def _advisory(
        self,
        contact: ContactRecord,
        pack: ScorePack,
        transcript: Transcript,
        decided: Scorecard,
    ) -> tuple[AdvisoryNote, ...]:
        """Ask the bound classifier for colour. Any failure means no colour, never no verdict."""
        if self._classifier is None:
            return ()
        utterances = tuple(
            turn.text
            for turn in transcript.turns
            if turn.role is ChannelRole.CUSTOMER and turn.text.strip()
        )[:_MAX_CLASSIFIER_UTTERANCES]
        request = SignalRequest(
            contact_id=contact.contact_id,
            locale=pack.locale,
            customer_utterances=utterances,
            detected_cue_ids=tuple(s.cue_id for s in decided.signals if s.detected),
        )
        try:
            return tuple(self._classifier.classify(request))
        except Exception:
            # An advisory sentence is not worth failing a compliance assessment for. The
            # verdict was complete before this call and is unchanged by its absence.
            return ()

    def _narrate(self, decided: Scorecard) -> Narration:
        """Draft a narrative, validate it against the engine's figures, else fall back."""
        if self._narrator is None:
            return deterministic_narration(decided)
        brief = narration_brief(decided)
        try:
            draft = self._narrator.narrate(brief)
        except Exception:
            draft = None
        return grounded_or_fallback(draft, brief, decided)

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def _record(self, scorecard: Scorecard, *, actor: str) -> None:
        """Write the already-redacted WORM record (P-04 / rule R2).

        The summary is composed from the ENGINE's figures, then masked again on the way in.
        The transcript was already redacted at ingestion, so this second pass is belt and
        braces on a record that cannot be edited afterwards.
        """
        unmet = ", ".join(f"{f.requirement_id}={f.status.value}" for f in scorecard.failing)
        summary = (
            f"{scorecard.contact_id}: {scorecard.disposition.value} on "
            f"{scorecard.market.value}/{scorecard.pack_id}@{scorecard.pack_version}; "
            f"disclosure {scorecard.disclosure_score:.2f}, "
            f"adherence {scorecard.adherence_score:.2f}; unmet: {unmet or 'none'}"
        )
        self._audit.record(
            AuditEvent(
                action="score_contact",
                actor=actor,
                decision=(
                    Decision.ESCALATED if scorecard.requires_human_review else Decision.ALLOWED
                ),
                severity=scorecard.severity,
                redacted_summary=redact(summary, PII_PATTERNS),
                citations=scorecard.citations,
                timestamp=scorecard.as_of,
            )
        )


def disposition_is_failing(disposition: Disposition) -> bool:
    """True for every disposition that is not a clean pass. One definition, used everywhere."""
    return disposition is not Disposition.COMPLIANT
