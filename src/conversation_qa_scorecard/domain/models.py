"""Vertical artifact models: the conversation-QA scorecard's own request and result types.

The artifacts THIS vertical produces, as opposed to the vertical-neutral machinery in
``kernel.py``. Every type here is frozen, stdlib-only and free of I/O, so the scoring engine
that builds them is replayable byte for byte.

Three ideas run through the whole module and each of them is a fail-closed decision:

* **A finding cites where it came from.** Every requirement outcome carries the turn index and
  the half-open character span in the ORIGINAL turn text that decided it, so a reviewer can be
  shown the words rather than a verdict. A finding with no evidence span is either an absence
  (nothing was said) or a gap (nothing was configured), and the two are different answers.
* **Absence and silence are different from a pass.** ``PRESENT`` is the only satisfied status.
  ``ABSENT``, ``OUT_OF_ORDER``, ``LATE``, ``PENDING``, ``UNVERIFIABLE`` and ``GAP`` all fail,
  and they say WHY they failed rather than collapsing into one boolean.
* **The engine owns the numbers.** Nothing here is computed by a model. The narration types
  below carry text a model may have drafted, and the schema check that lets it in refuses any
  figure the engine did not already publish.

The transcript, speaker-turn, word-offset, channel-role and redaction-span types are NOT
redeclared here: they come from the pinned ``speech-lexicon-kit`` and are re-exported once,
through ``ports/speech.py``, so three sibling systems keep meaning the same thing by "turn 4,
characters 12 to 34".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from hex_service_kit.enums import LenientStrEnum
from speech_lexicon_kit import AdherenceOutcome, ChannelRole

from .kernel import Citation, Decision, Severity

# --------------------------------------------------------------------------------------- #
# Taxonomies
# --------------------------------------------------------------------------------------- #


class Market(LenientStrEnum):
    """The regulatory market a contact is scored under (drives which pack applies)."""

    SG = "SG"
    AU = "AU"
    JP = "JP"


class RequirementKind(LenientStrEnum):
    """What a requirement is: a mandated disclosure, a script segment, or nothing configured.

    ``UNCONFIGURED`` exists so a ``GAP`` finding never has to lie about which kind it was.
    The contact declared an obligation and the pack defines none, so its kind is genuinely
    unknown, and the engine counts it against BOTH scores rather than guessing one: an
    obligation nobody configured could have been either, and neither ratio may claim it.
    """

    DISCLOSURE = "disclosure"
    SCRIPT_SEGMENT = "script_segment"
    UNCONFIGURED = "unconfigured"


class RequirementStatus(LenientStrEnum):
    """The outcome of one requirement. ``PRESENT`` is the ONLY satisfied value.

    The engine is fail-closed, so every uncertainty lands on one of the failing members and
    each of them names its own reason:

    * ``ABSENT``       - the wording was never matched on the required speaker's turns;
    * ``OUT_OF_ORDER`` - every step was said, but not in the mandated order, which for a
      disclosure sequence is the same harm as not saying one of them (a fee disclosure after
      the customer agreed is not a fee disclosure);
    * ``LATE``         - said, in order, but outside the configured timing window;
    * ``PENDING``      - not said yet, and the transcript is still open as of ``as_of``;
    * ``UNVERIFIABLE`` - said and in order, but a declared timing window cannot be checked
      because the transcript carries no word timings. An unchecked deadline is never reported
      as a met one;
    * ``GAP``          - the contact DECLARED this obligation and the active pack configures
      no requirement for it. Nothing was measured, so nothing may be claimed.
    """

    PRESENT = "present"
    ABSENT = "absent"
    OUT_OF_ORDER = "out_of_order"
    LATE = "late"
    PENDING = "pending"
    UNVERIFIABLE = "unverifiable"
    GAP = "gap"

    @property
    def satisfied(self) -> bool:
        """True only for :attr:`PRESENT`. Every other member is a failure with a reason."""
        return self is RequirementStatus.PRESENT


#: Kit adherence outcome -> this vertical's requirement status. ``OUT_OF_ORDER`` deliberately
#: does NOT map to a softer value: the plan's rule is that an unmatched OR out-of-order
#: disclosure is absent for scoring purposes, and the distinct status keeps the reason legible
#: to the reviewer while scoring identically.
ADHERENCE_TO_STATUS: dict[AdherenceOutcome, RequirementStatus] = {
    AdherenceOutcome.SATISFIED: RequirementStatus.PRESENT,
    AdherenceOutcome.LATE: RequirementStatus.LATE,
    AdherenceOutcome.OUT_OF_ORDER: RequirementStatus.OUT_OF_ORDER,
    AdherenceOutcome.ABSENT: RequirementStatus.ABSENT,
    AdherenceOutcome.PENDING: RequirementStatus.PENDING,
    AdherenceOutcome.UNVERIFIABLE: RequirementStatus.UNVERIFIABLE,
}


class Disposition(LenientStrEnum):
    """The scorecard's overall verdict. Pure code decides it; a model never does."""

    COMPLIANT = "compliant"
    REMEDIATE = "remediate"
    NON_COMPLIANT = "non_compliant"
    INDETERMINATE = "indeterminate"


class SignalKind(LenientStrEnum):
    """The two deterministic cue families this vertical scores from its own lexicons."""

    SENTIMENT = "sentiment"
    VULNERABILITY = "vulnerability"


#: Worst-first severity ordering, used to sort findings and to roll them up. Declared once so
#: every consumer sorts identically; a scorecard whose row order depended on dict iteration
#: would not be replayable.
SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}


# --------------------------------------------------------------------------------------- #
# The pack (configuration turned into domain values)
# --------------------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PhraseVariant:
    """One acceptable wording of a requirement step: the mandated phrase or a paraphrase."""

    phrase_id: str
    text: str
    #: True for the wording the regulator or the policy actually mandates; paraphrases are the
    #: approved alternatives a QA team accepts. Kept so a scorecard can say WHICH was heard.
    required: bool = False


@dataclass(frozen=True, slots=True)
class ScriptStep:
    """One named step of a requirement: an entry id plus every wording that satisfies it."""

    entry_id: str
    variants: tuple[PhraseVariant, ...]
    label: str = ""


@dataclass(frozen=True, slots=True)
class Requirement:
    """One configured obligation: what must be said, by whom, in what order, by when.

    Every field is CONFIGURATION read from the score pack. Nothing about a market, a product
    or a wording is compiled into the engine, so tuning a jurisdiction is a pack edit that a
    compliance officer can read and diff.
    """

    requirement_id: str
    kind: RequirementKind
    steps: tuple[ScriptStep, ...]
    severity: Severity
    citation: Citation
    #: Which speaker the wording must come from. ``None`` accepts any speaker, which is almost
    #: never what a disclosure wants: a customer reciting the fee schedule is not a disclosure.
    role: ChannelRole | None = None
    #: Deadline from the start of the conversation, in milliseconds. ``None`` means untimed.
    deadline_ms: int | None = None
    #: Maximum elapsed time from the first step to the last, in milliseconds.
    within_ms: int | None = None
    description: str = ""
    remediation: str = ""

    @property
    def entry_ids(self) -> tuple[str, ...]:
        return tuple(step.entry_id for step in self.steps)


@dataclass(frozen=True, slots=True)
class CueSet:
    """One deterministic cue family: a named signal and the phrases that evidence it.

    The per-vertical lexicon lives HERE (in the pack), not in the shared speech kernel: which
    words evidence financial hardship in a retail-banking call is reviewed vertical policy on
    a per-market schedule, and changing it must not need a release of a shared package.
    """

    cue_id: str
    kind: SignalKind
    label: str
    severity: Severity
    phrases: tuple[str, ...]
    #: For sentiment cues: +1 for a positive cue, -1 for a negative one, 0 for neutral. The
    #: engine sums these; it never asks a model what the customer was feeling.
    polarity: int = 0
    citation: Citation | None = None


@dataclass(frozen=True, slots=True)
class ScorePack:
    """One market's reviewed configuration: requirements, cues and the citations behind them.

    Immutable, so a scorecard can record the exact ``pack_id`` and ``version`` that produced
    it and a regulator can re-run the same assessment against the same pack.
    """

    pack_id: str
    version: str
    market: Market
    locale: str
    product: str
    requirements: tuple[Requirement, ...]
    cues: tuple[CueSet, ...]
    description: str = ""

    def requirement(self, requirement_id: str) -> Requirement | None:
        """The configured requirement with this id, or ``None`` (which the engine reads as GAP)."""
        for requirement in self.requirements:
            if requirement.requirement_id == requirement_id:
                return requirement
        return None

    def cues_of(self, kind: SignalKind) -> tuple[CueSet, ...]:
        return tuple(cue for cue in self.cues if cue.kind is kind)


# --------------------------------------------------------------------------------------- #
# The contact under assessment
# --------------------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ContactRecord:
    """One contact to score: who owns it, which market it belongs to, what it owed.

    ``declared_requirement_ids`` is the obligation set the CONTACT carries, taken from the
    product and market it was routed under. It is deliberately separate from the pack: a
    contact that owes a requirement the active pack does not configure is a ``GAP``, not a
    pass, and that comparison is only possible when the two lists are independent.
    """

    contact_id: str
    tenant: str
    market: Market
    product: str
    declared_requirement_ids: tuple[str, ...]
    agent_id: str = ""
    channel: str = "voice"
    audio_uri: str = ""
    locale: str = ""


# --------------------------------------------------------------------------------------- #
# Findings and the scorecard
# --------------------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    """Where in the transcript a finding came from: turn index and a half-open char span.

    This is what makes a finding citable. ``char_start``/``char_end`` index the ORIGINAL turn
    text (the kit maps a match found in normalised space back to the source range), so a UI
    can highlight the words that were actually spoken.
    """

    turn_index: int
    char_start: int
    char_end: int
    speaker_id: str
    role: ChannelRole
    text: str
    start_ms: int | None = None
    end_ms: int | None = None

    def __post_init__(self) -> None:
        if self.turn_index < 0:
            raise ValueError("turn_index must not be negative")
        if self.char_start < 0 or self.char_end < self.char_start:
            raise ValueError(
                f"evidence span {self.char_start}:{self.char_end} is not a half-open range"
            )


@dataclass(frozen=True, slots=True)
class RequirementFinding:
    """The outcome of one requirement, with the evidence (or the stated lack of it).

    ``evidence`` is empty exactly when nothing was matched, which is the honest rendering of
    ``ABSENT`` and of ``GAP``. A satisfied finding always carries at least one span.
    """

    requirement_id: str
    kind: RequirementKind
    status: RequirementStatus
    severity: Severity
    citation: Citation
    evidence: tuple[EvidenceSpan, ...] = ()
    missing_entry_ids: tuple[str, ...] = ()
    detail: str = ""
    remediation: str = ""
    elapsed_ms: int | None = None

    @property
    def satisfied(self) -> bool:
        return self.status.satisfied


@dataclass(frozen=True, slots=True)
class SignalFinding:
    """One deterministic cue family's result on this contact, with its evidence spans."""

    cue_id: str
    kind: SignalKind
    label: str
    severity: Severity
    detected: bool
    evidence: tuple[EvidenceSpan, ...] = ()
    polarity: int = 0


@dataclass(frozen=True, slots=True)
class AdvisoryNote:
    """Non-consequential colour a model adapter may add. It changes NO number and NO verdict.

    Kept in its own field of the scorecard so the "identical with the model stubbed out"
    check can compare everything else byte for byte and still let the advisory text exist.
    """

    source: str
    kind: SignalKind
    text: str
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class Narration:
    """Schema-validated narrative a model MAY produce, restating already-decided findings.

    It is admitted only when every figure it mentions is one the engine already published and
    every citation it names is one the engine already emitted. On any failure it is discarded
    and the deterministic fallback stands; a scorecard is never blocked on a model.
    """

    headline: str
    body: str
    citations: tuple[Citation, ...] = ()
    model: str = ""
    grounded: bool = True


@dataclass(frozen=True, slots=True)
class Scorecard:
    """The artifact: one contact's deterministic compliance and quality verdict.

    Every number below is computed by the engine from the findings. ``narration`` may be
    model-drafted; nothing else in this object ever is.
    """

    scorecard_id: str
    contact_id: str
    tenant: str
    market: Market
    pack_id: str
    pack_version: str
    as_of: datetime
    transcript_id: str
    disposition: Disposition
    decision: Decision
    severity: Severity
    requires_human_review: bool
    findings: tuple[RequirementFinding, ...]
    signals: tuple[SignalFinding, ...]
    disclosure_score: float
    adherence_score: float
    sentiment_score: int
    citations: tuple[Citation, ...] = ()
    advisory: tuple[AdvisoryNote, ...] = ()
    narration: Narration | None = None
    review_ref: str = ""
    engine_version: str = ""
    #: Turn count of the redacted transcript the engine scored, so a stored scorecard can be
    #: rendered without holding the transcript itself.
    turn_count: int = 0
    redaction_count: int = 0

    @property
    def failing(self) -> tuple[RequirementFinding, ...]:
        """Every requirement that did not pass, worst severity first (stable)."""
        return tuple(f for f in self.findings if not f.satisfied)

    @property
    def vulnerability_cue_ids(self) -> tuple[str, ...]:
        return tuple(
            s.cue_id for s in self.signals if s.detected and s.kind is SignalKind.VULNERABILITY
        )


@dataclass(frozen=True, slots=True)
class ScorecardRow:
    """The flat, warehouse-shaped projection of a scorecard (one row per contact).

    Deliberately flat and deliberately free of transcript text: a warehouse export is an
    analytics artifact, and shipping utterances into an analytics table is how a QA programme
    becomes a data-protection incident.
    """

    scorecard_id: str
    tenant: str
    contact_id: str
    market: str
    pack_id: str
    pack_version: str
    as_of: str
    disposition: str
    severity: str
    disclosure_score: float
    adherence_score: float
    sentiment_score: int
    failing_requirement_ids: tuple[str, ...] = ()
    vulnerability_cue_ids: tuple[str, ...] = ()
    requires_human_review: bool = False
    review_ref: str = ""


@dataclass(frozen=True, slots=True)
class ScoringRequest:
    """Everything one scoring run needs, gathered so the engine call stays a pure function.

    Whether the conversation is still open is deliberately NOT a field: the transcript already
    knows (``ended_at`` is unset), and a second source of truth for it would eventually
    disagree with the first about whether a missing disclosure is ``PENDING`` or ``ABSENT``.
    """

    contact: ContactRecord
    pack: ScorePack
    as_of: datetime
    #: How many identifiers the ingestion step masked. Carried on the request because masking
    #: shifts offsets, so the kernel drops the spans from the redacted copy on purpose; the
    #: count is still worth recording, and a scorecard that could not state it would be unable
    #: to show a reviewer that redaction ran at all.
    redaction_count: int = 0
    metadata: dict[str, str] = field(default_factory=dict)
