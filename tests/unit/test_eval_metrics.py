"""Every eval metric is proved able to go RED. A metric that cannot go red is not a metric.

This is the standing answer to the most expensive failure mode in this whole programme: a gate
that is green because it cannot fail. A redactor scored against its own output, a coverage
number computed from the pipeline's own verdict, a golden set that planted no target. Each of
those ships a confident green and proves nothing at all.

So each metric below is run twice: once on the CLEAN pipeline, which must pass its threshold,
and once against a MUTANT that reintroduces the exact defect the metric exists to catch, which
must fail it. ``agent_eval_kit.assert_can_go_red`` reports the two failures distinctly,
because a clean case that scores below the bar is a broken-pessimistic metric and a mutant that
scores above it is a falsely green one, and they need different fixes.

The mutants are deliberately the REAL defects, not synthetic noise:

* the engine that marks everything present (the "our adherence is 100%" bug);
* the span mapping that is off by one (a citation a reviewer follows to the wrong words);
* the cue lexicon that stopped matching (a hardship signal nobody sees);
* the narration gate that accepts whatever the model said;
* the review router that returns silently (rule R8 unwired, which is auto-execution);
* the pack with a requirement that binds no speaker (satisfied by the customer reading it back);
* the redactor switched off.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_eval_kit import assert_can_go_red
from pii_kit import redact

from conversation_qa_scorecard.config import (
    DEFAULT_BINDINGS,
    Settings,
)
from conversation_qa_scorecard.domain.models import (
    EvidenceSpan,
    Narration,
    RequirementStatus,
    Scorecard,
    ScorePack,
    SignalKind,
)
from conversation_qa_scorecard.domain.narration import (
    deterministic_narration,
    grounded_or_fallback,
)
from conversation_qa_scorecard.domain.pii import (
    PII_PATTERNS,
)
from conversation_qa_scorecard.ports.narration import (
    NarrationBrief,
)
from conversation_qa_scorecard.score_pack import (
    load_packs,
)

from tests import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "eval"))

import run_eval  # noqa: E402  (the eval script is the code under test, not a package)

_SETTINGS = Settings(profile="local", audit_path=":memory:", scorecard_path=":memory:")
_ROWS = run_eval.load_dataset(run_eval.DEFAULT_DATASET)
_CLEAN = run_eval.assess(_ROWS, _SETTINGS)

#: The silent router, bound the documented way: by dotted path in the adapters table.
_SILENT_ROUTER = "tests.fixtures.silent_router:SilentReviewRouter"


# --------------------------------------------------------------------------------------- #
# Mutants
# --------------------------------------------------------------------------------------- #
def _all_present(assessments: Sequence[Any]) -> list[Any]:
    """The engine that says every obligation was met. The bug every QA dashboard invites."""
    return [
        replace(
            assessment,
            scorecard=replace(
                assessment.scorecard,
                findings=tuple(
                    replace(finding, status=RequirementStatus.PRESENT)
                    for finding in assessment.scorecard.findings
                ),
            ),
        )
        for assessment in assessments
    ]


def _spans_off_by_one(assessments: Sequence[Any]) -> list[Any]:
    """The span mapping that drifts by one character. A citation to the wrong words."""

    def _shift(span: EvidenceSpan) -> EvidenceSpan:
        return replace(span, char_start=span.char_start + 1, char_end=span.char_end + 1)

    return [
        replace(
            assessment,
            scorecard=replace(
                assessment.scorecard,
                findings=tuple(
                    replace(finding, evidence=tuple(_shift(span) for span in finding.evidence))
                    for finding in assessment.scorecard.findings
                ),
            ),
        )
        for assessment in assessments
    ]


def _cues_never_fire(assessments: Sequence[Any]) -> list[Any]:
    """The vulnerability lexicon that stopped matching. Nobody notices, which is the point."""
    return [
        replace(
            assessment,
            scorecard=replace(
                assessment.scorecard,
                signals=tuple(
                    replace(signal, detected=False, evidence=())
                    if signal.kind is SignalKind.VULNERABILITY
                    else signal
                    for signal in assessment.scorecard.signals
                ),
            ),
        )
        for assessment in assessments
    ]


def _roleless_pack(packs: dict[str, ScorePack]) -> dict[str, ScorePack]:
    """A pack whose first requirement binds no speaker: the customer could satisfy it."""
    mutated = dict(packs)
    key = sorted(mutated)[0]
    pack = mutated[key]
    first, *rest = pack.requirements
    mutated[key] = replace(pack, requirements=(replace(first, role=None), *rest))
    return mutated


def _permissive_gate(
    draft: Narration | None, brief: NarrationBrief, scorecard: Scorecard
) -> Narration:
    """A gate that validates nothing and keeps whatever the model produced. The mutant.

    Same signature as the production gate, so the metric under test is the SAME code path with
    one substitution, rather than a second implementation that happens to score badly.
    """
    return draft if draft is not None else deterministic_narration(scorecard)


def _settings_with(router_target: str) -> Settings:
    """Rebind the review router by dotted path, the documented deployment path."""
    adapters = {port: dict(table) for port, table in DEFAULT_BINDINGS.items()}
    adapters["review_router"]["local"] = router_target
    return replace(_SETTINGS, adapters=adapters)


# --------------------------------------------------------------------------------------- #
# The proofs
# --------------------------------------------------------------------------------------- #
def test_pack_schema_validity_can_go_red() -> None:
    assert_can_go_red(
        run_eval.pack_schema_validity,
        green=load_packs(),
        red=_roleless_pack(load_packs()),
        threshold=run_eval.THRESHOLDS["pack_schema_validity"],
        metric="pack_schema_validity",
    )


def test_disclosure_presence_can_go_red() -> None:
    assert_can_go_red(
        run_eval.disclosure_presence,
        green=_CLEAN,
        red=_all_present(_CLEAN),
        threshold=run_eval.THRESHOLDS["disclosure_presence"],
        metric="disclosure_presence",
    )


def test_script_adherence_can_go_red() -> None:
    assert_can_go_red(
        run_eval.script_adherence,
        green=_CLEAN,
        red=_all_present(_CLEAN),
        threshold=run_eval.THRESHOLDS["script_adherence"],
        metric="script_adherence",
    )


def test_citation_accuracy_can_go_red() -> None:
    assert_can_go_red(
        run_eval.citation_accuracy,
        green=_CLEAN,
        red=_spans_off_by_one(_CLEAN),
        threshold=run_eval.THRESHOLDS["citation_accuracy"],
        metric="citation_accuracy",
    )


def test_vulnerability_recall_can_go_red() -> None:
    assert_can_go_red(
        run_eval.vulnerability_recall,
        green=_CLEAN,
        red=_cues_never_fire(_CLEAN),
        threshold=run_eval.THRESHOLDS["vulnerability_recall"],
        metric="vulnerability_recall",
    )


def test_narration_groundedness_can_go_red() -> None:
    """The mutant is the GATE, not the draft: a validator that accepts whatever it is given."""
    gates: dict[str, Any] = {"strict": grounded_or_fallback, "permissive": _permissive_gate}

    def _score(name: str) -> float:
        return run_eval.narration_groundedness(_CLEAN, gates[name])

    assert_can_go_red(
        _score,
        green="strict",
        red="permissive",
        threshold=run_eval.THRESHOLDS["narration_groundedness"],
        metric="narration_groundedness",
    )


def test_review_safety_can_go_red() -> None:
    """The mutant is rule R8 unwired: a router that accepts an escalation and returns silently."""

    def _score(settings: Settings) -> float:
        routed, _summaries = run_eval.review_safety(_ROWS, settings)
        return routed

    assert_can_go_red(
        _score,
        green=_SETTINGS,
        red=_settings_with(_SILENT_ROUTER),
        threshold=run_eval.THRESHOLDS["review_safety"],
        metric="review_safety",
    )


def test_pii_safety_can_go_red() -> None:
    """The mutant is the redactor switched off, on the SAME text the clean case redacted."""
    raw = "CT-SG-0004: remediate; caller quoted NRIC S1234567D and ops@meridian.example"

    def _score(summary: str) -> float:
        return run_eval.pii_safety([summary], ["S1234567D"])

    assert_can_go_red(
        _score,
        green=redact(raw, PII_PATTERNS),
        red=raw,
        threshold=run_eval.THRESHOLDS["pii_safety"],
        metric="pii_safety",
    )


def test_the_golden_labels_are_not_the_pipelines_own_output() -> None:
    """The independence claim, asserted rather than asserted about.

    The dataset is a committed FILE that predates any run, and the evidence it carries is a
    QUOTATION resolved with ``str.index``. If the labels were derived from the engine, every
    mutant above would still agree with them and every proof on this page would be theatre.
    """
    text = (REPO_ROOT / "eval" / "datasets" / "golden_scorecards.jsonl").read_text(encoding="utf-8")
    assert "expected_status" in text and "expected_evidence" in text
    for assessment in _CLEAN:
        for _requirement_id, (turn_index, quotation) in (
            assessment.label.get("expected_evidence") or {}
        ).items():
            turn = assessment.transcript.turns[int(turn_index)]
            assert str(quotation) in turn.text, (
                "a label quotes words that are not in the transcript, so the oracle is broken "
                "rather than independent"
            )


def test_the_eval_script_is_where_the_gate_says_it_is() -> None:
    """A guard against the sys.path insert above silently importing something else."""
    assert Path(run_eval.__file__).resolve() == (REPO_ROOT / "eval" / "run_eval.py").resolve()
