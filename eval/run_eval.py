#!/usr/bin/env python3
"""Evaluation gate for Conversation QA and Compliance Scorecard (E3).

Two named layers via ``--mode`` (the scaffold is ``agent_eval_kit.eval_main``):

* **smoke** (default) - the offline pre-merge check CI runs on every change: it drives the real
  pipeline (score pack, redaction, scoring engine, narration gate, review routing) against an
  independently labelled golden set with SDK-free local adapters, and scores eight metrics.
* **gate** - the promotion verdict from the shared Hrz4 authority (requires the ``gcp``
  profile), via ``agent_eval_kit.PromotionGateClient``.

Exit is ``0`` iff every metric meets its threshold (and, in gate mode, the authority agrees).

THE ORACLE IS INDEPENDENT, which is the only thing that makes any of this evidence. Every
metric scores the pipeline against ``eval/datasets/golden_scorecards.jsonl``, whose expectations
were written by reading the synthetic transcripts, and NEVER against the pipeline's own verdict.
Two consequences worth stating plainly:

* ``citation_accuracy`` resolves each labelled quotation to a character span with a plain
  ``str.index``, which is a different algorithm from the engine's normalise-fold-and-map-back.
  A span mapping that broke would disagree with the label rather than agreeing with itself.
* ``narration_groundedness`` is scored on ADVERSARIAL drafts built here, not on whatever the
  bound narrator happened to produce. A metric that only ever sees well-behaved output is a
  metric that cannot go red.

Every metric is proved able to go red in ``tests/unit/test_eval_metrics.py`` with
``agent_eval_kit.assert_can_go_red``. A metric that cannot go red is not a metric.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_eval_kit import EvalMetricResult, EvalReport, PromotionGateClient, eval_main
from hex_service_kit.netdefaults import read_env_setting
from pii_kit import pack_leak
from speech_lexicon_kit import Transcript

from conversation_qa_scorecard.adapters.local.audit import (
    LocalAuditAdapter,
)
from conversation_qa_scorecard.adapters.local.transcription import (
    FixtureTranscriptSource,
)
from conversation_qa_scorecard.config import (
    Settings,
    build_container,
)
from conversation_qa_scorecard.domain.ingestion import (
    redact_for_scoring,
)
from conversation_qa_scorecard.domain.kernel import (
    Citation,
)
from conversation_qa_scorecard.domain.models import (
    ContactRecord,
    Narration,
    Scorecard,
    ScorePack,
    ScoringRequest,
    SignalKind,
)
from conversation_qa_scorecard.domain.narration import (
    deterministic_narration,
    grounded_or_fallback,
    narration_brief,
)
from conversation_qa_scorecard.domain.pii import (
    PII_PATTERNS,
)
from conversation_qa_scorecard.domain.scoring_engine import (
    ScoringEngine,
)
from conversation_qa_scorecard.ports.narration import (
    NarrationBrief,
)
from conversation_qa_scorecard.score_pack import (
    load_packs,
    pack_for_market,
)
from conversation_qa_scorecard.service import (
    build_service,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = _REPO_ROOT / "eval" / "datasets" / "golden_scorecards.jsonl"

#: The assessment moment. Explicit, because the engine takes ``as_of`` and an eval whose
#: verdicts drifted with today's date would fail on a Monday for no reason anybody could find.
AS_OF = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)

THRESHOLDS: dict[str, float] = {
    "pack_schema_validity": 1.0,
    "disclosure_presence": 0.95,
    "script_adherence": 0.95,
    "citation_accuracy": 0.99,
    "vulnerability_recall": 0.95,
    "narration_groundedness": 1.0,
    "review_safety": 1.0,
    "pii_safety": 0.99,
}
#: The registered Hrz4 metric bundle for this vertical (Hrz4 owns the metrics + thresholds).
_BUNDLE = "conversation-qa-scorecard"

_QUALITY_URL_ENV = "CONVQA_QUALITY_URL"
_DEFAULT_QUALITY_URL = "http://localhost:8084"


# --------------------------------------------------------------------------------------- #
# The scored run: one real assessment per labelled contact
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Assessment:
    """One contact: what the label says, and what the pipeline actually produced."""

    label: dict[str, Any]
    contact: ContactRecord
    pack: ScorePack
    transcript: Transcript
    scorecard: Scorecard


def load_dataset(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))
    if not rows:
        raise SystemExit(f"{path}: golden dataset is empty")
    return rows


def assess(rows: Sequence[dict[str, Any]], settings: Settings) -> list[Assessment]:
    """Run the REAL engine over every labelled contact. No stubs and no shortcuts."""
    source = FixtureTranscriptSource(settings)
    catalogue = {contact.contact_id: contact for contact in source.contacts()}
    engine = ScoringEngine()
    out: list[Assessment] = []
    for row in rows:
        contact = catalogue[str(row["contact_id"])]
        pack = pack_for_market(settings, contact.market, contact.product)
        redacted = redact_for_scoring(
            source.fetch(contact.contact_id, locale=contact.locale, audio_uri=contact.audio_uri),
            PII_PATTERNS,
        )
        scorecard = engine.score(
            ScoringRequest(contact=contact, pack=pack, as_of=AS_OF, redaction_count=redacted.count),
            redacted.transcript,
        )
        out.append(
            Assessment(
                label=row,
                contact=contact,
                pack=pack,
                transcript=redacted.transcript,
                scorecard=scorecard,
            )
        )
    return out


def _mean(scores: Sequence[float]) -> float:
    return round(sum(scores) / len(scores), 4) if scores else 0.0


# --------------------------------------------------------------------------------------- #
# Metric 1: the pack itself is valid (config, not code, so it needs its own gate)
# --------------------------------------------------------------------------------------- #
def pack_schema_validity(packs: dict[str, ScorePack]) -> float:
    """Fraction of shipped packs that satisfy every structural invariant, INDEPENDENTLY.

    Deliberately not "did the loader raise". The loader could be wrong; this re-states the
    invariants as assertions over the loaded values, so a loosened check in the loader shows up
    here rather than passing quietly. A pack is the only thing between a mandated wording and a
    scorecard, so it gets its own metric and its threshold is 1.0.
    """
    if not packs:
        return 0.0
    scores: list[float] = []
    for pack in packs.values():
        problems = pack_problems(pack)
        scores.append(0.0 if problems else 1.0)
    return _mean(scores)


def pack_problems(pack: ScorePack) -> list[str]:
    """Every structural violation in one pack, named. Empty means the pack is well formed."""
    problems: list[str] = []
    if not pack.requirements:
        problems.append(f"{pack.pack_id}: no requirements, so nothing can ever be measured")
    if not pack.cues:
        problems.append(f"{pack.pack_id}: no cue lexicon, so no signal can ever fire")
    if not pack.version:
        problems.append(f"{pack.pack_id}: no version, so a scorecard cannot record what scored it")
    seen: set[str] = set()
    for requirement in pack.requirements:
        rid = requirement.requirement_id
        if rid in seen:
            problems.append(f"{rid}: duplicate requirement id makes the scorecard ambiguous")
        seen.add(rid)
        if not requirement.steps:
            problems.append(f"{rid}: no steps, so it can never be satisfied")
        if requirement.role is None:
            problems.append(f"{rid}: no role, so the customer could satisfy the disclosure")
        if not requirement.citation.source_id or not requirement.citation.title:
            problems.append(f"{rid}: no named instrument behind the requirement")
        for window in (requirement.deadline_ms, requirement.within_ms):
            if window is not None and window <= 0:
                problems.append(f"{rid}: a timing window of {window} never opens")
        for step in requirement.steps:
            if not step.variants:
                problems.append(f"{rid}/{step.entry_id}: no wording, so it can never match")
            if not any(variant.required for variant in step.variants):
                problems.append(f"{rid}/{step.entry_id}: no mandated wording, only paraphrases")
            if any(not variant.text.strip() for variant in step.variants):
                problems.append(f"{rid}/{step.entry_id}: an empty wording matches every turn")
    for cue in pack.cues:
        if not cue.phrases:
            problems.append(f"{pack.pack_id}/{cue.cue_id}: a cue with no phrases never fires")
        if cue.polarity not in (-1, 0, 1):
            problems.append(f"{pack.pack_id}/{cue.cue_id}: polarity {cue.polarity} is not a sign")
    return problems


# --------------------------------------------------------------------------------------- #
# Metrics 2 and 3: the engine's status against the INDEPENDENT label
# --------------------------------------------------------------------------------------- #
def _status_accuracy(assessments: Sequence[Assessment], kinds: tuple[str, ...]) -> float:
    """Per-requirement agreement with the label, restricted to the requested kinds.

    Scored per REQUIREMENT rather than per contact: a contact-level "did the disposition
    match" hides five right answers behind one wrong one, and the thing under test is whether
    each individual obligation was read correctly.
    """
    scores: list[float] = []
    for assessment in assessments:
        expected = assessment.label.get("expected_status") or {}
        by_id = {finding.requirement_id: finding for finding in assessment.scorecard.findings}
        for requirement_id, expected_status in expected.items():
            finding = by_id.get(requirement_id)
            if finding is None:
                scores.append(0.0)
                continue
            if finding.kind.value not in kinds:
                continue
            scores.append(1.0 if finding.status.value == expected_status else 0.0)
    return _mean(scores)


def disclosure_presence(assessments: Sequence[Assessment]) -> float:
    """Did each mandated DISCLOSURE get the status the label says it deserves?

    ``unconfigured`` is scored here too: a declared obligation the pack does not configure is a
    GAP, and reading it as anything else is exactly the failure this metric exists to catch.
    """
    return _status_accuracy(assessments, ("disclosure", "unconfigured"))


def script_adherence(assessments: Sequence[Assessment]) -> float:
    """Did each ordered SCRIPT SEGMENT get the status the label says it deserves?"""
    return _status_accuracy(assessments, ("script_segment",))


# --------------------------------------------------------------------------------------- #
# Metric 4: the citation points at the words the label quotes
# --------------------------------------------------------------------------------------- #
def _expected_span(transcript: Transcript, turn_index: int, quotation: str) -> tuple[int, int]:
    """Resolve a labelled quotation to a span with str.index. Not the engine's algorithm."""
    text = transcript.turns[turn_index].text
    start = text.find(quotation)
    if start < 0:
        return (-1, -1)
    return (start, start + len(quotation))


def citation_accuracy(assessments: Sequence[Assessment]) -> float:
    """Does every finding cite the exact turn and characters the label quotes?

    A finding whose verdict is right and whose citation points somewhere else is worse than no
    citation at all: a reviewer follows it, reads the wrong words and approves.
    """
    scores: list[float] = []
    for assessment in assessments:
        expected = assessment.label.get("expected_evidence") or {}
        by_id = {finding.requirement_id: finding for finding in assessment.scorecard.findings}
        for requirement_id, (turn_index, quotation) in expected.items():
            finding = by_id.get(requirement_id)
            if finding is None or not finding.evidence:
                scores.append(0.0)
                continue
            wanted = _expected_span(assessment.transcript, int(turn_index), str(quotation))
            actual = [
                (span.turn_index, span.char_start, span.char_end) for span in finding.evidence
            ]
            hit = (int(turn_index), wanted[0], wanted[1]) in actual
            quoted = any(span.text == quotation for span in finding.evidence)
            scores.append(1.0 if (hit and quoted and wanted[0] >= 0) else 0.0)
    return _mean(scores)


# --------------------------------------------------------------------------------------- #
# Metric 5: the deterministic vulnerability lexicon finds what a human labelled
# --------------------------------------------------------------------------------------- #
def vulnerability_recall(assessments: Sequence[Assessment]) -> float:
    """Recall of the labelled vulnerability cues, per contact.

    Recall rather than precision, deliberately: a missed hardship signal reaches a customer who
    needed help and did not get it, while a spurious one reaches a reviewer who dismisses it.
    A contact with no labelled cue scores 1.0 only when the engine also found none, so the
    metric cannot be gamed by a lexicon that fires on everything.
    """
    scores: list[float] = []
    for assessment in assessments:
        expected = set(assessment.label.get("expected_vulnerability_cues") or ())
        found = {
            signal.cue_id
            for signal in assessment.scorecard.signals
            if signal.detected and signal.kind is SignalKind.VULNERABILITY
        }
        if not expected:
            scores.append(1.0 if not found else 0.0)
            continue
        scores.append(len(expected & found) / len(expected))
    return _mean(scores)


# --------------------------------------------------------------------------------------- #
# Metric 6: a narration may restate the engine and may not compute
# --------------------------------------------------------------------------------------- #
def _hallucinated(scorecard: Scorecard) -> Narration:
    """A draft that invents a figure the engine never published. The adversarial case."""
    return Narration(
        headline=f"Assessment for {scorecard.contact_id}",
        body=(
            "Overall adherence was 97 per cent across 1234 reviewed contacts, "
            "with no material issues identified."
        ),
        citations=(),
        model="adversarial-fixture",
    )


#: The instrument id no pack defines. Named once, because the metric asserts on its ABSENCE
#: from the accepted citations and a typo would make that assertion vacuous.
_INVENTED_SOURCE_ID = "not_in_this_pack"


def _miscited(scorecard: Scorecard) -> Narration:
    """A draft whose PROSE is fine and whose citation names an instrument nobody cited."""
    return replace(
        deterministic_narration(scorecard),
        citations=(Citation(source_id=_INVENTED_SOURCE_ID, title="Invented instrument"),),
        model="adversarial-fixture",
    )


#: The signature of a narration gate: take a draft, the brief and the scorecard, and return the
#: narration that is actually kept. The production gate is ``grounded_or_fallback``; the metric
#: takes it as a PARAMETER so ``tests/unit/test_eval_metrics.py`` can run the same metric against
#: a permissive gate and prove the metric goes red rather than merely asserting that it would.
NarrationGate = Callable[[Narration | None, NarrationBrief, Scorecard], Narration]


def narration_groundedness(
    assessments: Sequence[Assessment],
    gate: NarrationGate = grounded_or_fallback,
) -> float:
    """Two halves, and both must hold, or the gate proves nothing.

    * an INVENTED FIGURE must be rejected: the draft's prose must not survive, because the
      figure is in the prose;
    * an INVENTED INSTRUMENT must be rejected: here the prose is the deterministic text itself,
      so the tell is the CITATION, and asserting on the body would silently score nothing;
    * the deterministic fallback itself must be ACCEPTED. Without that half the metric is
      satisfied by a validator that rejects everything, which is not groundedness, it is
      switching the model off and calling it a control.
    """
    scores: list[float] = []
    for assessment in assessments:
        scorecard = assessment.scorecard
        brief = narration_brief(scorecard)

        invented = _hallucinated(scorecard)
        kept = gate(invented, brief, scorecard)
        scores.append(0.0 if kept.body == invented.body else 1.0)

        miscited = _miscited(scorecard)
        kept_citations = {
            citation.source_id for citation in gate(miscited, brief, scorecard).citations
        }
        scores.append(0.0 if _INVENTED_SOURCE_ID in kept_citations else 1.0)

        clean = deterministic_narration(scorecard)
        kept_clean = gate(clean, brief, scorecard)
        scores.append(1.0 if kept_clean.body == clean.body else 0.0)
    return _mean(scores)


# --------------------------------------------------------------------------------------- #
# Metric 7: rule R8, end to end on the real service
# --------------------------------------------------------------------------------------- #
def review_safety(rows: Sequence[dict[str, Any]], settings: Settings) -> tuple[float, list[str]]:
    """Every scorecard the label says must be reviewed is FLAGGED and ROUTED, and no other is.

    Driven through the real ``ScorecardService``, not the engine alone, because the thing under
    test is the wiring: a flag nobody routes is auto-execution with extra steps. The returned
    audit summaries feed ``pii_safety``, so both metrics score the same real run.
    """
    container = build_container(settings)
    service = build_service(container)
    catalogue = {contact.contact_id: contact for contact in container.transcription.contacts()}
    scores: list[float] = []
    for row in rows:
        contact = catalogue[str(row["contact_id"])]
        scorecard = service.score_contact(
            contact,
            pack_for_market(settings, contact.market, contact.product),
            actor="eval-bot",
            tenant=contact.tenant,
            as_of=AS_OF,
        )
        expected = bool(row["expected_requires_human_review"])
        flagged = scorecard.requires_human_review is expected
        routed = bool(scorecard.review_ref) is expected
        scores.append(1.0 if (flagged and routed) else 0.0)
    audit = container.audit
    assert isinstance(audit, LocalAuditAdapter)
    summaries = [str(entry.get("redacted_summary", "")) for entry in audit.log.read_all()]
    return _mean(scores), summaries


def pii_safety(summaries: Sequence[str], planted: Sequence[str]) -> float:
    """No identifier may survive into an audit record, by the pack rows OR by planted literal.

    Two independent oracles on purpose: the pack scan catches whatever the shared rows catch,
    and the planted-literal check fires even if a row is broken. One of them alone is a metric
    that agrees with the thing it is testing.
    """
    pack_leaked = any(pack_leak(text, PII_PATTERNS) for text in summaries)
    literal_leaked = any(token in text for token in planted for text in summaries)
    return 0.0 if (pack_leaked or literal_leaked) else 1.0


# --------------------------------------------------------------------------------------- #
# The offline smoke run
# --------------------------------------------------------------------------------------- #
def run_smoke(dataset: Path) -> EvalReport:
    rows = load_dataset(dataset)
    settings = Settings(profile="local", audit_path=":memory:", scorecard_path=":memory:")
    assessments = assess(rows, settings)
    routed, summaries = review_safety(rows, settings)
    planted = [str(row["planted"]) for row in rows if row.get("planted")]

    scored = {
        "pack_schema_validity": pack_schema_validity(load_packs()),
        "disclosure_presence": disclosure_presence(assessments),
        "script_adherence": script_adherence(assessments),
        "citation_accuracy": citation_accuracy(assessments),
        "vulnerability_recall": vulnerability_recall(assessments),
        "narration_groundedness": narration_groundedness(assessments),
        "review_safety": routed,
        "pii_safety": pii_safety(summaries, planted),
    }
    results = tuple(
        EvalMetricResult.scored(metric, score, THRESHOLDS[metric])
        for metric, score in scored.items()
    )
    return EvalReport(dataset=str(dataset), results=results, n_examples=len(rows))


def run_gate(dataset: Path) -> tuple[EvalReport, bool]:
    settings = Settings.load()
    if settings.profile != "gcp":
        raise SystemExit(
            "--mode gate is the promotion authority and requires "
            f"CONVQA_PROFILE=gcp (got {settings.profile!r}); "
            "run --mode smoke for the offline pre-merge check."
        )
    quality = read_env_setting(_QUALITY_URL_ENV)
    if quality.is_configured_empty:
        raise SystemExit(
            f"{_QUALITY_URL_ENV} is set to an empty value, which names no authority. "
            "Unset it to use the default, or point it at the Hrz4 quality service."
        )
    client = PromotionGateClient(
        quality.value if quality.has_value else _DEFAULT_QUALITY_URL,
        bundle=_BUNDLE,
        model="gemini-3.5-flash",
    )
    return client.evaluate(str(dataset)), client.gate(str(dataset))


if __name__ == "__main__":
    raise SystemExit(
        eval_main(
            smoke=run_smoke,
            gate=run_gate,
            default_dataset=DEFAULT_DATASET,
            description="Offline / Hrz4 evaluation gate for E3.",
        )
    )
