"""Scorecard to JSON and back, in pure stdlib, so the record outlives this codebase.

``hex_service_kit.serialization.to_jsonable`` handles the outbound half for every dataclass in
the tree. This module owns the INBOUND half, which the commons deliberately does not provide:
rebuilding typed domain values from a stored document needs to know the types, and knowing the
types is the domain's job.

Why it exists at all, rather than pickling or storing an ORM row: a compliance record has to be
readable by somebody who does not have this service. The stored form is plain JSON with plain
enum values, so an auditor can read a scorecard in a text editor, and a migration off this
platform is a file copy rather than a rewrite (P-12). :func:`scorecard_from_jsonable` is the
proof that the round trip is lossless, and ``tests/unit/test_scorecard_store.py`` asserts it.

Loading is strict. An unknown status, kind, disposition or severity RAISES rather than
defaulting, because a stored scorecard that silently reloads with a different verdict than it
was written with is worse than one that fails to load.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from speech_lexicon_kit import ChannelRole

from .kernel import Citation, Decision, Severity
from .models import (
    AdvisoryNote,
    Disposition,
    EvidenceSpan,
    Market,
    Narration,
    RequirementFinding,
    RequirementKind,
    RequirementStatus,
    Scorecard,
    ScorecardRow,
    SignalFinding,
    SignalKind,
)


def _str(node: Any, key: str, default: str = "") -> str:
    value = node.get(key, default)
    return default if value is None else str(value)


def _citation(raw: Any) -> Citation:
    data = raw or {}
    return Citation(
        source_id=_str(data, "source_id"),
        title=_str(data, "title"),
        snippet=_str(data, "snippet"),
    )


def _evidence(raw: Any) -> EvidenceSpan:
    data = raw or {}
    return EvidenceSpan(
        turn_index=int(data.get("turn_index", 0)),
        char_start=int(data.get("char_start", 0)),
        char_end=int(data.get("char_end", 0)),
        speaker_id=_str(data, "speaker_id"),
        role=ChannelRole(_str(data, "role", ChannelRole.UNKNOWN.value)),
        text=_str(data, "text"),
        start_ms=None if data.get("start_ms") is None else int(data["start_ms"]),
        end_ms=None if data.get("end_ms") is None else int(data["end_ms"]),
    )


def _finding(raw: Any) -> RequirementFinding:
    data = raw or {}
    return RequirementFinding(
        requirement_id=_str(data, "requirement_id"),
        kind=RequirementKind(_str(data, "kind")),
        status=RequirementStatus(_str(data, "status")),
        severity=Severity(_str(data, "severity")),
        citation=_citation(data.get("citation")),
        evidence=tuple(_evidence(item) for item in data.get("evidence") or ()),
        missing_entry_ids=tuple(str(item) for item in data.get("missing_entry_ids") or ()),
        detail=_str(data, "detail"),
        remediation=_str(data, "remediation"),
        elapsed_ms=None if data.get("elapsed_ms") is None else int(data["elapsed_ms"]),
    )


def _signal(raw: Any) -> SignalFinding:
    data = raw or {}
    return SignalFinding(
        cue_id=_str(data, "cue_id"),
        kind=SignalKind(_str(data, "kind")),
        label=_str(data, "label"),
        severity=Severity(_str(data, "severity")),
        detected=bool(data.get("detected", False)),
        evidence=tuple(_evidence(item) for item in data.get("evidence") or ()),
        polarity=int(data.get("polarity", 0)),
    )


def _advisory(raw: Any) -> AdvisoryNote:
    data = raw or {}
    return AdvisoryNote(
        source=_str(data, "source"),
        kind=SignalKind(_str(data, "kind")),
        text=_str(data, "text"),
        confidence=float(data.get("confidence", 0.0)),
    )


def _narration(raw: Any) -> Narration | None:
    if not raw:
        return None
    return Narration(
        headline=_str(raw, "headline"),
        body=_str(raw, "body"),
        citations=tuple(_citation(item) for item in raw.get("citations") or ()),
        model=_str(raw, "model"),
        grounded=bool(raw.get("grounded", True)),
    )


def scorecard_from_jsonable(data: dict[str, Any]) -> Scorecard:
    """Rebuild a :class:`Scorecard` from the document ``to_jsonable`` produced."""
    return Scorecard(
        scorecard_id=_str(data, "scorecard_id"),
        contact_id=_str(data, "contact_id"),
        tenant=_str(data, "tenant"),
        market=Market(_str(data, "market")),
        pack_id=_str(data, "pack_id"),
        pack_version=_str(data, "pack_version"),
        as_of=datetime.fromisoformat(_str(data, "as_of")),
        transcript_id=_str(data, "transcript_id"),
        disposition=Disposition(_str(data, "disposition")),
        decision=Decision(_str(data, "decision")),
        severity=Severity(_str(data, "severity")),
        requires_human_review=bool(data.get("requires_human_review", False)),
        findings=tuple(_finding(item) for item in data.get("findings") or ()),
        signals=tuple(_signal(item) for item in data.get("signals") or ()),
        disclosure_score=float(data.get("disclosure_score", 0.0)),
        adherence_score=float(data.get("adherence_score", 0.0)),
        sentiment_score=int(data.get("sentiment_score", 0)),
        citations=tuple(_citation(item) for item in data.get("citations") or ()),
        advisory=tuple(_advisory(item) for item in data.get("advisory") or ()),
        narration=_narration(data.get("narration")),
        review_ref=_str(data, "review_ref"),
        engine_version=_str(data, "engine_version"),
        turn_count=int(data.get("turn_count", 0)),
        redaction_count=int(data.get("redaction_count", 0)),
    )


def scorecard_to_row(scorecard: Scorecard) -> ScorecardRow:
    """Project a scorecard into its flat warehouse row.

    Deliberately drops every utterance and every evidence span's text: an analytics table is
    read, joined and exported by people who never saw the retention policy, so the evidence
    stays in the tenant-scoped store behind the 403 and only the shape of the answer leaves.
    """
    return ScorecardRow(
        scorecard_id=scorecard.scorecard_id,
        tenant=scorecard.tenant,
        contact_id=scorecard.contact_id,
        market=scorecard.market.value,
        pack_id=scorecard.pack_id,
        pack_version=scorecard.pack_version,
        as_of=scorecard.as_of.isoformat(),
        disposition=scorecard.disposition.value,
        severity=scorecard.severity.value,
        disclosure_score=scorecard.disclosure_score,
        adherence_score=scorecard.adherence_score,
        sentiment_score=scorecard.sentiment_score,
        failing_requirement_ids=tuple(f.requirement_id for f in scorecard.failing),
        vulnerability_cue_ids=scorecard.vulnerability_cue_ids,
        requires_human_review=scorecard.requires_human_review,
        review_ref=scorecard.review_ref,
    )
