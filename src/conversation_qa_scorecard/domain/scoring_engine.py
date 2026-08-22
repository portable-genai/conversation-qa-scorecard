"""ScoringEngine: the deterministic conversation-QA verdict. Every number, every disposition.

The heart of E3. Given a redacted transcript, the contact's declared obligations and the
active score pack, it decides which mandated disclosures were made, in what order, inside
which timing window, what deterministic cues the conversation carries, and what the whole
contact's disposition is. A model may narrate the result for a reviewer; it never classifies
a requirement, never produces a score and never decides a disposition.

Purity and determinism
----------------------
Stdlib plus the pinned speech kernel, no clock and no I/O. ``as_of`` is an explicit parameter,
so replaying last quarter's assessment reproduces last quarter's verdict. Findings are sorted
by a total key (failing first, then worst severity, then requirement id), citations are
de-duplicated and sorted, and the scorecard id is a digest of the inputs and the outcome, so
two runs of the same contact against the same pack produce the same identifier.

Fail-closed, in six places that each cost a real scorecard a pass
----------------------------------------------------------------
* an obligation the CONTACT declared that the ACTIVE PACK does not configure is a ``GAP``:
  nothing was measured, so nothing may be claimed;
* an unmatched sequence is ``ABSENT``, and an out-of-order one is ``OUT_OF_ORDER`` and scores
  exactly as absent does. A fee disclosure made after the customer agreed is not a fee
  disclosure;
* a requirement with a declared timing window and a transcript with no word timings is
  ``UNVERIFIABLE``, never a pass: an unchecked deadline is not a met one;
* a contact with no declared obligations at all scores 0.0 and is ``INDETERMINATE``. An empty
  requirement set is silence, and silence must not be indistinguishable from compliance;
* a requirement with no configured role accepts any speaker, so the pack loader refuses one
  (see ``score_pack.py``); the engine additionally never credits a step to the customer when
  the pack named the agent;
* a detected vulnerability cue at HIGH or CRITICAL forces at least ``REMEDIATE`` and human
  review, whatever the disclosure findings say.

Configuration, not code (practice B4)
-------------------------------------
Nothing in this module knows what Singapore, Australia or Japan require, what wording counts
as a recording notice, or which words evidence financial hardship. Requirements, acceptable
paraphrases, ordering, timing windows, severities, citations and cue lexicons are all data on
the :class:`ScorePack`, loaded from a YAML pack a compliance officer can read and diff.
"""

from __future__ import annotations

from dataclasses import dataclass

from speech_lexicon_kit import (
    AdherenceOutcome,
    ChannelRole,
    Lexicon,
    LexiconEntry,
    LexiconHit,
    PhraseSequenceRequirement,
    PhraseSpec,
    SequenceAdherence,
    Transcript,
    digest,
    evaluate_requirements,
    find_hits,
    span_timing,
)

from .kernel import Citation, Decision, Severity
from .models import (
    ADHERENCE_TO_STATUS,
    SEVERITY_RANK,
    AdvisoryNote,
    CueSet,
    Disposition,
    EvidenceSpan,
    Narration,
    Requirement,
    RequirementFinding,
    RequirementKind,
    RequirementStatus,
    Scorecard,
    ScorePack,
    ScoringRequest,
    SignalFinding,
    SignalKind,
)

#: Bumped whenever a change here could move a verdict. Recorded on every scorecard, so an
#: auditor comparing two assessments can tell a policy change from an engine change.
ENGINE_VERSION = "qa-scoring/1"

#: The severity assigned to a declared obligation the pack does not configure. It is the top
#: band deliberately: "we did not check" is the most dangerous answer a QA programme can give,
#: because it looks exactly like "nothing was wrong" on every dashboard that counts failures.
GAP_SEVERITY = Severity.CRITICAL

#: Cue severities that make a detected vulnerability signal consequential on their own.
_CONSEQUENTIAL_CUE_SEVERITIES = (Severity.HIGH, Severity.CRITICAL)

#: Statuses that mean the engine could not conclude, as opposed to concluding a failure.
_INDETERMINATE_STATUSES = (
    RequirementStatus.GAP,
    RequirementStatus.UNVERIFIABLE,
    RequirementStatus.PENDING,
)

#: Failing statuses that are a definite breach rather than an unfinished measurement.
_BREACH_SEVERITIES = (Severity.CRITICAL, Severity.HIGH)

_CUE_PREFIX = "cue:"


def _ratio(satisfied: int, total: int) -> float:
    """Fraction satisfied, or 0.0 when nothing was measured (silence is not compliance)."""
    return round(satisfied / total, 4) if total else 0.0


def requirements_lexicon(pack: ScorePack) -> Lexicon:
    """Compile the pack's requirement wordings into one kit lexicon.

    Each :class:`ScriptStep` becomes one lexicon entry, and every acceptable wording of that
    step (the mandated phrase and each approved paraphrase) becomes one phrase under it. That
    is what makes "acceptable paraphrases" configuration rather than code: adding one is a
    pack edit, and the matcher never changes.
    """
    entries: dict[str, LexiconEntry] = {}
    for requirement in pack.requirements:
        for step in requirement.steps:
            if step.entry_id in entries:
                continue
            entries[step.entry_id] = LexiconEntry(
                entry_id=step.entry_id,
                phrases=tuple(
                    PhraseSpec(phrase_id=variant.phrase_id, text=variant.text)
                    for variant in step.variants
                ),
                tags=("required",) if any(v.required for v in step.variants) else (),
            )
    return Lexicon(
        lexicon_id=f"{pack.pack_id}:requirements",
        locale=pack.locale,
        entries=tuple(entries[key] for key in sorted(entries)),
        version=pack.version,
    )


def cue_lexicon(pack: ScorePack) -> Lexicon:
    """Compile the pack's per-vertical cue lexicons into one kit lexicon.

    Entry ids are namespaced with ``cue:`` so a cue can never be mistaken for a requirement
    step by the adherence pass, and vice versa.
    """
    return Lexicon(
        lexicon_id=f"{pack.pack_id}:cues",
        locale=pack.locale,
        entries=tuple(
            LexiconEntry(
                entry_id=_CUE_PREFIX + cue.cue_id,
                phrases=tuple(
                    PhraseSpec(phrase_id=f"{cue.cue_id}-{index}", text=phrase)
                    for index, phrase in enumerate(cue.phrases)
                ),
                tags=(cue.kind.value,),
            )
            for cue in sorted(pack.cues, key=lambda c: c.cue_id)
        ),
        version=pack.version,
    )


def _evidence(transcript: Transcript, hit: LexiconHit) -> EvidenceSpan:
    """Turn a kit hit into a citable evidence span in the ORIGINAL turn text."""
    turn = transcript.turn(hit.turn_index)
    start_ms, end_ms = span_timing(turn, hit.char_start, hit.char_end)
    return EvidenceSpan(
        turn_index=hit.turn_index,
        char_start=hit.char_start,
        char_end=hit.char_end,
        speaker_id=hit.speaker_id,
        role=hit.role,
        text=turn.text[hit.char_start : hit.char_end],
        start_ms=hit.start_ms if hit.start_ms is not None else start_ms,
        end_ms=hit.end_ms if hit.end_ms is not None else end_ms,
    )


def _as_kit_requirement(requirement: Requirement) -> PhraseSequenceRequirement:
    return PhraseSequenceRequirement(
        requirement_id=requirement.requirement_id,
        step_entry_ids=requirement.entry_ids,
        role=requirement.role,
        within_ms=requirement.within_ms,
        deadline_ms=requirement.deadline_ms,
    )


def _detail(status: RequirementStatus, adherence: SequenceAdherence | None) -> str:
    """A one-line, human-readable reason. Never a number the scorecard does not also carry."""
    if status is RequirementStatus.GAP:
        return "the contact declares this obligation and the active pack configures no requirement"
    if adherence is None:  # pragma: no cover - GAP is the only adherence-free status
        return ""
    if status is RequirementStatus.ABSENT:
        missing = ", ".join(adherence.missing_entry_ids) or "every step"
        return f"not said on the required speaker's turns: {missing}"
    if status is RequirementStatus.OUT_OF_ORDER:
        return "every step was said, but not in the mandated order"
    if status is RequirementStatus.LATE:
        return "said in order, but outside the configured timing window"
    if status is RequirementStatus.PENDING:
        return "not said yet, and the conversation is still open as of the assessment time"
    if status is RequirementStatus.UNVERIFIABLE:
        return "said in order, but the transcript carries no word timings to check the window"
    return "said, in order, by the required speaker, inside the configured window"


@dataclass(frozen=True, slots=True)
class ScoringEngine:
    """Pure, deterministic conversation-QA scorer. Owns every number and every verdict."""

    def score(
        self,
        request: ScoringRequest,
        transcript: Transcript,
        *,
        advisory: tuple[AdvisoryNote, ...] = (),
        narration: Narration | None = None,
    ) -> Scorecard:
        """Score one contact. ``advisory`` and ``narration`` are carried, never consulted.

        The two model-sourced arguments are attached to the result AFTER every number and the
        disposition have been decided, and nothing below reads them. That is what makes the
        "identical with the model adapter stubbed out" check possible: pass ``()`` and
        ``None`` and every other field is byte-identical.
        """
        pack = request.pack
        hits = find_hits(transcript, requirements_lexicon(pack))
        cue_hits = find_hits(transcript, cue_lexicon(pack))

        findings = self._requirement_findings(request, transcript, hits)
        signals = self._signal_findings(pack.cues, transcript, cue_hits)

        disclosure_score = _ratio(
            sum(1 for f in findings if f.satisfied and f.kind is RequirementKind.DISCLOSURE),
            sum(1 for f in findings if f.kind is not RequirementKind.SCRIPT_SEGMENT),
        )
        adherence_score = _ratio(
            sum(1 for f in findings if f.satisfied and f.kind is RequirementKind.SCRIPT_SEGMENT),
            sum(1 for f in findings if f.kind is not RequirementKind.DISCLOSURE),
        )
        sentiment_score = sum(s.polarity for s in signals if s.detected)

        disposition = self._disposition(findings, signals)
        severity = self._severity(findings, signals)
        escalate = disposition is not Disposition.COMPLIANT
        citations = self._citations(findings, signals, pack.cues)

        scorecard_id = self._scorecard_id(request, transcript, findings, signals)
        return Scorecard(
            scorecard_id=scorecard_id,
            contact_id=request.contact.contact_id,
            tenant=request.contact.tenant,
            market=request.contact.market,
            pack_id=pack.pack_id,
            pack_version=pack.version,
            as_of=request.as_of,
            transcript_id=transcript.transcript_id,
            disposition=disposition,
            decision=Decision.ESCALATED if escalate else Decision.ALLOWED,
            severity=severity,
            requires_human_review=escalate,
            findings=findings,
            signals=signals,
            disclosure_score=disclosure_score,
            adherence_score=adherence_score,
            sentiment_score=sentiment_score,
            citations=citations,
            advisory=advisory,
            narration=narration,
            engine_version=ENGINE_VERSION,
            turn_count=len(transcript.turns),
            redaction_count=request.redaction_count,
        )

    # ------------------------------------------------------------------ #
    # Requirements
    # ------------------------------------------------------------------ #
    def _requirement_findings(
        self,
        request: ScoringRequest,
        transcript: Transcript,
        hits: tuple[LexiconHit, ...],
    ) -> tuple[RequirementFinding, ...]:
        """One finding per DECLARED obligation. An unconfigured one is a GAP, never a pass."""
        pack = request.pack
        declared = tuple(dict.fromkeys(request.contact.declared_requirement_ids))
        configured = {rid: pack.requirement(rid) for rid in declared}
        scored = [
            _as_kit_requirement(requirement)
            for requirement in (configured[rid] for rid in declared)
            if requirement is not None
        ]
        # The kit refuses an empty requirement set nothing and refuses duplicate ids loudly;
        # both are wanted, so the only thing added here is the empty short-circuit, because
        # "no requirement was configured" is answered by the GAP branch below, not by an
        # adherence report over nothing.
        by_id: dict[str, SequenceAdherence] = {}
        if scored:
            report = evaluate_requirements(transcript, hits, scored, as_of=request.as_of)
            by_id = {result.requirement_id: result for result in report.results}

        findings: list[RequirementFinding] = []
        for requirement_id in declared:
            requirement = configured[requirement_id]
            if requirement is None:
                findings.append(
                    RequirementFinding(
                        requirement_id=requirement_id,
                        kind=RequirementKind.UNCONFIGURED,
                        status=RequirementStatus.GAP,
                        severity=GAP_SEVERITY,
                        citation=Citation(
                            source_id=f"pack:{pack.pack_id}",
                            title=f"Score pack {pack.pack_id} version {pack.version}",
                            snippet="obligation declared by the contact, absent from the pack",
                        ),
                        detail=_detail(RequirementStatus.GAP, None),
                        remediation=(
                            "configure this requirement in the market's score pack, then re-run "
                            "the assessment; until then the contact is unmeasured"
                        ),
                    )
                )
                continue
            findings.append(
                self._finding(requirement, by_id[requirement_id], transcript),
            )
        return tuple(
            sorted(
                findings,
                key=lambda f: (f.satisfied, SEVERITY_RANK[f.severity], f.requirement_id),
            )
        )

    @staticmethod
    def _finding(
        requirement: Requirement,
        adherence: SequenceAdherence,
        transcript: Transcript,
    ) -> RequirementFinding:
        status = ADHERENCE_TO_STATUS[AdherenceOutcome(adherence.outcome)]
        evidence = tuple(
            _evidence(transcript, step.hit) for step in adherence.steps if step.hit is not None
        )
        return RequirementFinding(
            requirement_id=requirement.requirement_id,
            kind=requirement.kind,
            status=status,
            severity=requirement.severity,
            citation=requirement.citation,
            evidence=evidence,
            missing_entry_ids=adherence.missing_entry_ids,
            detail=_detail(status, adherence),
            remediation="" if status.satisfied else requirement.remediation,
            elapsed_ms=adherence.elapsed_ms,
        )

    # ------------------------------------------------------------------ #
    # Deterministic signals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _signal_findings(
        cues: tuple[CueSet, ...],
        transcript: Transcript,
        cue_hits: tuple[LexiconHit, ...],
    ) -> tuple[SignalFinding, ...]:
        """One finding per configured cue family, with the spans that evidence it.

        Cues are matched on the CUSTOMER's turns and on turns with no assigned role: a
        customer saying "I have lost my job" is the signal, and an agent repeating it back is
        not a second one. Agent turns are excluded so a scripted empathy line cannot create
        a vulnerability finding out of nothing.
        """
        by_entry: dict[str, list[LexiconHit]] = {}
        for hit in cue_hits:
            if hit.role is ChannelRole.AGENT:
                continue
            by_entry.setdefault(hit.entry_id, []).append(hit)
        findings: list[SignalFinding] = []
        for cue in sorted(cues, key=lambda c: c.cue_id):
            matched = by_entry.get(_CUE_PREFIX + cue.cue_id, [])
            findings.append(
                SignalFinding(
                    cue_id=cue.cue_id,
                    kind=cue.kind,
                    label=cue.label,
                    severity=cue.severity,
                    detected=bool(matched),
                    evidence=tuple(_evidence(transcript, hit) for hit in matched),
                    polarity=cue.polarity,
                )
            )
        return tuple(findings)

    # ------------------------------------------------------------------ #
    # Roll-up
    # ------------------------------------------------------------------ #
    @staticmethod
    def _disposition(
        findings: tuple[RequirementFinding, ...],
        signals: tuple[SignalFinding, ...],
    ) -> Disposition:
        """The verdict, worst-first. Nothing here consults a model or a threshold table."""
        if not findings:
            # No declared obligation at all: nothing was measured, so nothing is claimed.
            return Disposition.INDETERMINATE
        if any(f.status in _INDETERMINATE_STATUSES for f in findings):
            return Disposition.INDETERMINATE
        failing = [f for f in findings if not f.satisfied]
        if any(f.severity in _BREACH_SEVERITIES for f in failing):
            return Disposition.NON_COMPLIANT
        consequential_cue = any(
            s.detected
            and s.kind is SignalKind.VULNERABILITY
            and s.severity in _CONSEQUENTIAL_CUE_SEVERITIES
            for s in signals
        )
        if failing or consequential_cue:
            return Disposition.REMEDIATE
        return Disposition.COMPLIANT

    @staticmethod
    def _severity(
        findings: tuple[RequirementFinding, ...],
        signals: tuple[SignalFinding, ...],
    ) -> Severity:
        """The worst severity actually in play: unmet obligations and detected cues both count."""
        candidates = [f.severity for f in findings if not f.satisfied]
        candidates += [
            s.severity for s in signals if s.detected and s.kind is SignalKind.VULNERABILITY
        ]
        if not findings:
            candidates.append(GAP_SEVERITY)
        if not candidates:
            return Severity.LOW
        return min(candidates, key=lambda severity: SEVERITY_RANK[severity])

    @staticmethod
    def _citations(
        findings: tuple[RequirementFinding, ...],
        signals: tuple[SignalFinding, ...],
        cues: tuple[CueSet, ...],
    ) -> tuple[Citation, ...]:
        """Every distinct instrument behind this scorecard, sorted by source id.

        A cue family contributes its citation only when it was DETECTED: citing the guidance
        behind a signal nobody triggered would pad the evidence list with instruments this
        contact never engaged.
        """
        seen: dict[str, Citation] = {}
        for finding in findings:
            seen.setdefault(finding.citation.source_id, finding.citation)
        detected = {signal.cue_id for signal in signals if signal.detected}
        for cue in cues:
            if cue.cue_id in detected and cue.citation is not None:
                seen.setdefault(cue.citation.source_id, cue.citation)
        return tuple(seen[key] for key in sorted(seen))

    @staticmethod
    def _scorecard_id(
        request: ScoringRequest,
        transcript: Transcript,
        findings: tuple[RequirementFinding, ...],
        signals: tuple[SignalFinding, ...],
    ) -> str:
        """A deterministic id: the same contact, pack, transcript and outcome hash the same.

        Idempotency matters here for a real reason: a re-scored contact must UPDATE its
        scorecard rather than accumulate near-duplicates a reviewer then has to reconcile.
        """
        material = {
            "tenant": request.contact.tenant,
            "contact_id": request.contact.contact_id,
            "pack": f"{request.pack.pack_id}@{request.pack.version}",
            "transcript_id": transcript.transcript_id,
            "as_of": request.as_of.isoformat(),
            "engine": ENGINE_VERSION,
            "findings": [[f.requirement_id, f.status.value, f.severity.value] for f in findings],
            "signals": [[s.cue_id, s.detected] for s in signals],
        }
        # The kit prefixes its digest with the algorithm; the id keeps the hex only, so
        # it stays a usable path segment and a document key in every store.
        return "sc-" + digest(material).rsplit(":", 1)[-1][:24]
