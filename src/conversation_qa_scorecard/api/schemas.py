"""API request/response schemas (Pydantic) mapped to and from the pure-domain models.

The wire shape is deliberately close to the domain shape: a scorecard is a compliance record,
and a reviewer, an auditor and a UI should all be able to read the same document. The one
thing the wire adds is ``review_ref``, which says where an escalation WENT.

The request carries no tenant and no actor. Both come from the verified principal, server side;
a client-asserted actor is discarded before it reaches the domain (see ``api/app.py``).
"""

from __future__ import annotations

from pydantic import BaseModel

from ..domain.models import Scorecard


class ScoreRequest(BaseModel):
    """Score one contact. The tenant is the verified principal's and is never taken from here."""

    contact_id: str


class CitationModel(BaseModel):
    source_id: str
    title: str
    snippet: str = ""


class EvidenceModel(BaseModel):
    """Where a finding came from: the turn and the half-open span in that turn's text."""

    turn_index: int
    char_start: int
    char_end: int
    speaker_id: str
    role: str
    text: str
    start_ms: int | None = None
    end_ms: int | None = None


class FindingModel(BaseModel):
    requirement_id: str
    kind: str
    status: str
    severity: str
    detail: str = ""
    remediation: str = ""
    citation: CitationModel
    evidence: list[EvidenceModel] = []
    missing_entry_ids: list[str] = []
    elapsed_ms: int | None = None


class SignalModel(BaseModel):
    cue_id: str
    kind: str
    label: str
    severity: str
    detected: bool
    polarity: int = 0
    evidence: list[EvidenceModel] = []


class AdvisoryModel(BaseModel):
    """Advisory colour from a model. It changes no number and no verdict."""

    source: str
    kind: str
    text: str
    confidence: float = 0.0


class NarrationModel(BaseModel):
    headline: str
    body: str
    model: str = ""
    grounded: bool = True
    citations: list[CitationModel] = []


class ScorecardResponse(BaseModel):
    scorecard_id: str
    contact_id: str
    tenant: str
    market: str
    pack_id: str
    pack_version: str
    as_of: str
    transcript_id: str
    disposition: str
    decision: str
    severity: str
    requires_human_review: bool
    disclosure_score: float
    adherence_score: float
    sentiment_score: int
    turn_count: int = 0
    redaction_count: int = 0
    engine_version: str = ""
    #: Where the escalation WENT (rule R8): the human-review-console review id, or the local queue
    #: reference.
    #: Empty only when the scorecard passed. A caller can tell a routed escalation from a flag
    #: that stopped here, which is the whole point of the rule.
    review_ref: str = ""
    findings: list[FindingModel] = []
    signals: list[SignalModel] = []
    citations: list[CitationModel] = []
    advisory: list[AdvisoryModel] = []
    narration: NarrationModel | None = None

    @classmethod
    def from_domain(cls, scorecard: Scorecard) -> ScorecardResponse:
        return cls(
            scorecard_id=scorecard.scorecard_id,
            contact_id=scorecard.contact_id,
            tenant=scorecard.tenant,
            market=scorecard.market.value,
            pack_id=scorecard.pack_id,
            pack_version=scorecard.pack_version,
            as_of=scorecard.as_of.isoformat(),
            transcript_id=scorecard.transcript_id,
            disposition=scorecard.disposition.value,
            decision=scorecard.decision.value,
            severity=scorecard.severity.value,
            requires_human_review=scorecard.requires_human_review,
            disclosure_score=scorecard.disclosure_score,
            adherence_score=scorecard.adherence_score,
            sentiment_score=scorecard.sentiment_score,
            turn_count=scorecard.turn_count,
            redaction_count=scorecard.redaction_count,
            engine_version=scorecard.engine_version,
            review_ref=scorecard.review_ref,
            findings=[
                FindingModel(
                    requirement_id=finding.requirement_id,
                    kind=finding.kind.value,
                    status=finding.status.value,
                    severity=finding.severity.value,
                    detail=finding.detail,
                    remediation=finding.remediation,
                    citation=_citation(finding.citation),
                    evidence=[_evidence(span) for span in finding.evidence],
                    missing_entry_ids=list(finding.missing_entry_ids),
                    elapsed_ms=finding.elapsed_ms,
                )
                for finding in scorecard.findings
            ],
            signals=[
                SignalModel(
                    cue_id=signal.cue_id,
                    kind=signal.kind.value,
                    label=signal.label,
                    severity=signal.severity.value,
                    detected=signal.detected,
                    polarity=signal.polarity,
                    evidence=[_evidence(span) for span in signal.evidence],
                )
                for signal in scorecard.signals
            ],
            citations=[_citation(citation) for citation in scorecard.citations],
            advisory=[
                AdvisoryModel(
                    source=note.source,
                    kind=note.kind.value,
                    text=note.text,
                    confidence=note.confidence,
                )
                for note in scorecard.advisory
            ],
            narration=(
                None
                if scorecard.narration is None
                else NarrationModel(
                    headline=scorecard.narration.headline,
                    body=scorecard.narration.body,
                    model=scorecard.narration.model,
                    grounded=scorecard.narration.grounded,
                    citations=[_citation(c) for c in scorecard.narration.citations],
                )
            ),
        )


class ContactModel(BaseModel):
    """A contact the offline profile can score, for the demo and the UI's picker."""

    contact_id: str
    tenant: str
    market: str
    product: str
    declared_requirement_ids: list[str] = []
    headline: str = ""


class HealthResponse(BaseModel):
    status: str
    profile: str
    region: str
    #: Provenance the UI banner states on every page: where the runtime sits and which model
    #: answers. Both are read off the service because the browser cannot know either.
    runtime: str = "local"  # "gcp" | "local"
    generator_model: str = "deterministic-offline-stub"


def _citation(citation: object) -> CitationModel:
    return CitationModel(
        source_id=getattr(citation, "source_id", ""),
        title=getattr(citation, "title", ""),
        snippet=getattr(citation, "snippet", ""),
    )


def _evidence(span: object) -> EvidenceModel:
    return EvidenceModel(
        turn_index=getattr(span, "turn_index", 0),
        char_start=getattr(span, "char_start", 0),
        char_end=getattr(span, "char_end", 0),
        speaker_id=getattr(span, "speaker_id", ""),
        role=str(getattr(span, "role", "")),
        text=getattr(span, "text", ""),
        start_ms=getattr(span, "start_ms", None),
        end_ms=getattr(span, "end_ms", None),
    )
