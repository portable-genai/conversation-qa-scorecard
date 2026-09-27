"""Narration grounding: the schema that lets a model restate findings and nothing else.

The model's whole job on this service is to turn a decided scorecard into a paragraph a QA
manager can read. This module is the gate it has to pass, and the gate is deliberately crude
and total rather than clever and partial:

* **Every figure in the draft must be a figure the engine published.** The allowed set is
  computed from the scorecard itself, once, and carried in the brief so the prompt and the
  validator cannot disagree. A draft claiming "97% adherence" is rejected because ``97`` is not
  in that set. It does not matter whether 97 was a plausible rounding of the real number: a
  narration that invents a figure has invented a compliance statistic.
* **Every citation in the draft must be a citation the engine emitted.** A narrator may not
  attach an instrument the findings did not cite, because a cited claim is the only kind this
  service ships.
* **Rejection is not an error.** The scorecard was complete before the narrator was called, so
  a rejected draft falls back to :func:`deterministic_narration`, which is composed by pure
  code from the same figures and passes the same gate by construction. That fallback is what
  makes "the scorecard is identical with the model stubbed out" true for the consequential
  fields.

Why a closed numeric allowlist rather than a similarity score: a QA scorecard's numbers are the
product. A groundedness check that admits "approximately right" admits a wrong number, and a
wrong number in a compliance report is the failure mode the whole determinism rule exists to
prevent.
"""

from __future__ import annotations

import re

from ..ports.narration import NarrationBrief
from .errors import NarrationRejectedError
from .kernel import Citation
from .models import Disposition, Narration, Scorecard, SignalKind

#: Any run of digits, optionally with a decimal part. Deliberately greedy about what counts as
#: a figure: it is better to reject a draft for mentioning a year than to admit one that
#: invented an adherence percentage.
_FIGURE = re.compile(r"\d+(?:\.\d+)?")

#: The one-line verdict sentence per disposition. Pure text, no figures, so it can never be the
#: thing that fails the grounding check.
_VERDICT_TEXT: dict[Disposition, str] = {
    Disposition.COMPLIANT: "Every declared obligation was met.",
    Disposition.REMEDIATE: "Obligations were met with exceptions that need remediation.",
    Disposition.NON_COMPLIANT: "A mandated disclosure was not made as required.",
    Disposition.INDETERMINATE: "The contact could not be fully assessed.",
}


def figures_in(text: str) -> frozenset[str]:
    """Every numeric token in ``text``, as written."""
    return frozenset(_FIGURE.findall(text or ""))


def allowed_figures(scorecard: Scorecard) -> frozenset[str]:
    """The closed set of numeric tokens a narration may contain for this scorecard.

    Each entry is a figure the ENGINE produced, written both as the engine holds it and as a
    narrator would naturally render it (a ratio and its percentage), because rejecting a draft
    for saying "80%" when the engine holds ``0.8`` would reject correct narrations and teach
    nobody anything.
    """
    tokens: set[str] = set()
    for ratio in (scorecard.disclosure_score, scorecard.adherence_score):
        tokens.update(figures_in(f"{ratio}"))
        tokens.update(figures_in(f"{ratio * 100:.0f}"))
        tokens.update(figures_in(f"{ratio:.2f}"))
    for count in (
        scorecard.sentiment_score,
        abs(scorecard.sentiment_score),
        len(scorecard.findings),
        len(scorecard.failing),
        len(scorecard.vulnerability_cue_ids),
        scorecard.turn_count,
        scorecard.redaction_count,
    ):
        tokens.add(str(count))
    for finding in scorecard.findings:
        tokens.update(figures_in(finding.requirement_id))
        if finding.elapsed_ms is not None:
            tokens.add(str(finding.elapsed_ms))
        for span in finding.evidence:
            tokens.add(str(span.turn_index))
    for signal in scorecard.signals:
        tokens.update(figures_in(signal.cue_id))
    tokens.update(figures_in(scorecard.pack_version))
    tokens.update(figures_in(scorecard.pack_id))
    tokens.update(figures_in(scorecard.as_of.isoformat()))
    tokens.update(figures_in(scorecard.contact_id))
    tokens.update(figures_in(scorecard.engine_version))
    return frozenset(tokens)


def narration_brief(scorecard: Scorecard) -> NarrationBrief:
    """Build the ONLY material a narrator is allowed to see. Already decided, already redacted."""
    return NarrationBrief(
        scorecard_id=scorecard.scorecard_id,
        contact_id=scorecard.contact_id,
        market=scorecard.market.value,
        disposition=scorecard.disposition.value,
        severity=scorecard.severity.value,
        disclosure_score=scorecard.disclosure_score,
        adherence_score=scorecard.adherence_score,
        sentiment_score=scorecard.sentiment_score,
        failing_requirements=tuple(
            (finding.requirement_id, finding.status.value, finding.severity.value)
            for finding in scorecard.failing
        ),
        detected_cues=tuple(
            (signal.cue_id, signal.kind.value) for signal in scorecard.signals if signal.detected
        ),
        citation_ids=tuple(citation.source_id for citation in scorecard.citations),
        allowed_figures=allowed_figures(scorecard),
        locale=scorecard.market.value,
    )


def deterministic_narration(scorecard: Scorecard) -> Narration:
    """Compose the fallback narrative from the engine's own output, in pure code.

    It is what a caller gets when no narrator is bound, when the narrator declines, and when
    the narrator's draft fails the grounding gate. It passes :func:`validate_narration` by
    construction, and ``tests/unit/test_narration_grounding.py`` asserts that it does, so the
    gate can never be strict enough to reject the service's own fallback.
    """
    failing = scorecard.failing
    vulnerable = [s for s in scorecard.signals if s.detected and s.kind is SignalKind.VULNERABILITY]
    parts = [_VERDICT_TEXT[scorecard.disposition]]
    parts.append(f"Of {len(scorecard.findings)} declared obligations, {len(failing)} did not pass.")
    if failing:
        listed = ", ".join(f"{f.requirement_id} ({f.status.value})" for f in failing)
        parts.append(f"Unmet: {listed}.")
    if vulnerable:
        parts.append(
            "Vulnerability cues detected: " + ", ".join(sorted(s.label for s in vulnerable)) + "."
        )
    parts.append(
        f"Disclosure coverage {scorecard.disclosure_score:.2f}, "
        f"script adherence {scorecard.adherence_score:.2f}."
    )
    if scorecard.requires_human_review:
        parts.append("This scorecard is routed to a human reviewer and executes nothing.")
    return Narration(
        headline=f"{scorecard.disposition.value.replace('_', ' ')}: {scorecard.contact_id}",
        body=" ".join(parts),
        citations=scorecard.citations,
        model="deterministic",
        grounded=True,
    )


def validate_narration(draft: Narration, brief: NarrationBrief) -> Narration:
    """Return ``draft`` when it is grounded, or raise :class:`NarrationRejectedError`.

    Two independent checks, and both name what was wrong, because "the model was rejected" is
    not something an operator can act on.
    """
    invented = figures_in(draft.headline + " " + draft.body) - brief.allowed_figures
    if invented:
        raise NarrationRejectedError(
            "narration mentions figures the engine did not publish: "
            + ", ".join(sorted(invented))
            + ". A narration may restate the scorecard and may not compute one."
        )
    unknown = {c.source_id for c in draft.citations} - set(brief.citation_ids)
    if unknown:
        raise NarrationRejectedError(
            "narration cites instruments the findings did not: " + ", ".join(sorted(unknown))
        )
    return Narration(
        headline=draft.headline,
        body=draft.body,
        citations=tuple(_ordered_citations(draft.citations)),
        model=draft.model,
        grounded=True,
    )


def _ordered_citations(citations: tuple[Citation, ...]) -> list[Citation]:
    seen: dict[str, Citation] = {}
    for citation in citations:
        seen.setdefault(citation.source_id, citation)
    return [seen[key] for key in sorted(seen)]


def grounded_or_fallback(
    draft: Narration | None,
    brief: NarrationBrief,
    scorecard: Scorecard,
) -> Narration:
    """The whole narration policy in one call: take the draft if it is grounded, else fall back.

    A rejected or absent draft is never an error to the caller. The consequential answer was
    complete before the narrator ran, so the service degrades to its own deterministic prose
    rather than failing a compliance assessment because a model was unreachable.
    """
    if draft is None:
        return deterministic_narration(scorecard)
    try:
        return validate_narration(draft, brief)
    except NarrationRejectedError:
        rejected = deterministic_narration(scorecard)
        return Narration(
            headline=rejected.headline,
            body=rejected.body,
            citations=rejected.citations,
            model=f"deterministic (rejected draft from {draft.model or 'model'})",
            grounded=True,
        )
