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
6. **Rule R1: the guardrail screens both generation calls, both directions.** The prompt each
   call sends is screened INPUT before the call, exactly as sent (``narration_prompt`` /
   ``signal_prompt``, the same functions the managed adapters send), so every caller-controlled
   field in it (the contact id, the market, the customer's own words) is screened. What comes
   back is screened OUTPUT before it is validated, attached, audited or returned, and the text
   the screen hands back is the text used from then on. A refusal, including a guardrail that
   raised instead of deciding (fail closed), is audited ``Decision.BLOCKED`` and the model's
   text is never used: the narrator falls back to the deterministic summary (fixed text built
   from the engine's figures) and the classifier contributes no advisory note. Both calls are
   optional by design (rule 3), so that fallback is the whole answer, never a partial one.

Nothing here computes a score, a status or a disposition. That is all in ``scoring_engine.py``,
which is pure and takes an explicit ``as_of``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from pii_kit import redact
from speech_lexicon_kit import ChannelRole, Transcript

from ..ports.audit import AuditSinkPort
from ..ports.guardrail import GuardrailPort
from ..ports.narration import NarrationPort, narration_prompt
from ..ports.observability import ObservabilityTracerPort
from ..ports.review_router import ReviewRouterPort
from ..ports.scorecard_store import ScorecardStorePort
from ..ports.signals import SignalClassifierPort, SignalRequest, signal_prompt
from ..ports.speech import TranscriptSourcePort
from ..ports.warehouse import WarehouseExportPort
from .errors import ScorecardNotFoundError, TenantAccessDeniedError
from .ingestion import redact_for_scoring
from .kernel import AuditEvent, Decision, Direction, GuardrailVerdict
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

#: The two generation steps rule R1 screens, as named in a BLOCKED audit record, and what the
#: service used in place of the refused model text.
_NARRATION_STEP = "narration"
_CLASSIFIER_STEP = "signal_classifier"
_FALLBACK: dict[str, str] = {
    _NARRATION_STEP: "the deterministic summary stands",
    _CLASSIFIER_STEP: "no advisory note attached",
}


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
        guardrail: GuardrailPort,
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
        #: Rule R1. Required, never optional: a service built without a screen would run both
        #: generation calls unscreened. With the switch off the container binds
        #: :class:`~..adapters.controls.DisabledGuardrail`, a stated pass-through, not a gap.
        self._guardrail = guardrail

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

            advisory = self._advisory(contact, pack, redacted.transcript, decided, actor=actor)
            narration = self._narrate(decided, actor=actor)
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
        *,
        actor: str,
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
        # Rule R1, INPUT: the prompt exactly as the classifier sends it, before any customer
        # text reaches a model.
        prompt = signal_prompt(request)
        screened_prompt = self._screen(
            prompt, Direction.INPUT, step=_CLASSIFIER_STEP, decided=decided, actor=actor
        )
        if screened_prompt is None or not self._unchanged(
            screened_prompt, prompt, step=_CLASSIFIER_STEP, decided=decided, actor=actor
        ):
            return ()
        try:
            notes = tuple(self._classifier.classify(request))
        except Exception:
            # An advisory sentence is not worth failing a compliance assessment for. The
            # verdict was complete before this call and is unchanged by its absence.
            return ()
        # Rule R1, OUTPUT: every note before it is attached to the scorecard, and the screened
        # text is the text attached. A refused note is dropped; an emptied one says nothing.
        screened: list[AdvisoryNote] = []
        for note in notes:
            text = self._screen(
                note.text, Direction.OUTPUT, step=_CLASSIFIER_STEP, decided=decided, actor=actor
            )
            if text:
                screened.append(replace(note, text=text))
        return tuple(screened)

    def _narrate(self, decided: Scorecard, *, actor: str) -> Narration:
        """Draft a narrative, validate it against the engine's figures, else fall back."""
        if self._narrator is None:
            return deterministic_narration(decided)
        brief = narration_brief(decided)
        # Rule R1, INPUT: the prompt exactly as the narrator sends it, before any drafting.
        prompt = narration_prompt(brief)
        screened_prompt = self._screen(
            prompt, Direction.INPUT, step=_NARRATION_STEP, decided=decided, actor=actor
        )
        if screened_prompt is None or not self._unchanged(
            screened_prompt, prompt, step=_NARRATION_STEP, decided=decided, actor=actor
        ):
            return deterministic_narration(decided)
        try:
            draft = self._narrator.narrate(brief)
        except Exception:
            draft = None
        if draft is None:
            return grounded_or_fallback(None, brief, decided)
        # Rule R1, OUTPUT: headline and body before the draft is validated, audited or
        # returned, and the screened text is the text validated. A refusal in either discards
        # the whole draft: the deterministic summary stands, exactly as for a rejected draft.
        headline = self._screen(
            draft.headline, Direction.OUTPUT, step=_NARRATION_STEP, decided=decided, actor=actor
        )
        body = (
            None
            if headline is None
            else self._screen(
                draft.body, Direction.OUTPUT, step=_NARRATION_STEP, decided=decided, actor=actor
            )
        )
        if headline is None or body is None:
            return deterministic_narration(decided)
        return grounded_or_fallback(replace(draft, headline=headline, body=body), brief, decided)

    # ------------------------------------------------------------------ #
    # The guardrail (rule R1)
    # ------------------------------------------------------------------ #
    def _screen(
        self,
        text: str,
        direction: Direction,
        *,
        step: str,
        decided: Scorecard,
        actor: str,
    ) -> str | None:
        """Screen one text; return the text to use from here on, or ``None`` after a refusal.

        The returned text is the verdict's ``sanitized_text`` exactly as given, including an
        empty string: a screen that redacted everything has not asked for the original back.
        A block, and a guardrail that raised instead of deciding, are both refusals (fail
        closed): each is audited BLOCKED before ``None`` tells the caller to fall back.
        """
        try:
            verdict: GuardrailVerdict = self._guardrail.screen(text, direction)
        except Exception as exc:
            self._audit_blocked(
                step, direction, f"guardrail unavailable ({type(exc).__name__})", decided, actor
            )
            return None
        if not verdict.allowed or verdict.sanitized_text is None:
            reason = verdict.reason or f"{step} {direction.value} blocked by guardrail"
            self._audit_blocked(step, direction, reason, decided, actor)
            return None
        return verdict.sanitized_text

    def _unchanged(
        self, screened: str, sent: str, *, step: str, decided: Scorecard, actor: str
    ) -> bool:
        """True when an INPUT screen handed the prompt back unchanged; a rewrite is refused.

        The model adapters build their request from the structured brief or request, not from
        a string the domain passes them, so a prompt the screen rewrote could not be the one
        sent. Sending the unscreened original instead would defeat the screen, so a rewrite is
        audited BLOCKED like a match. Neither bound screen rewrites a prompt today (Model Armor
        reports a decision for these filters, the local heuristic allows or blocks).
        """
        if screened == sent:
            return True
        self._audit_blocked(
            step,
            Direction.INPUT,
            "the guardrail rewrote the prompt; only an unchanged prompt can be sent",
            decided,
            actor,
        )
        return False

    def _audit_blocked(
        self, step: str, direction: Direction, reason: str, decided: Scorecard, actor: str
    ) -> None:
        """Audit a guardrail refusal (rule R1/R2) BEFORE the caller falls back.

        Never carries the refused text: only which generation step was refused, in which
        direction, why, and what stood in its place. It names the scorecard by its id, which
        the engine derives, rather than by the contact id, which the caller supplied and which
        may itself be what was refused.
        """
        fallback = _FALLBACK[step]
        self._audit.record(
            AuditEvent(
                action=f"{step}_screen",
                actor=actor,
                decision=Decision.BLOCKED,
                severity=decided.severity,
                redacted_summary=redact(
                    f"{decided.scorecard_id}: {step} {direction.value} blocked: {reason}; "
                    f"{fallback}",
                    PII_PATTERNS,
                ),
                citations=(),
                timestamp=decided.as_of,
            )
        )

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
