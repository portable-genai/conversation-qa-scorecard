"""A market's disclosure wording is reviewed ONCE, in one artifact, by both systems that use it.

The contact-centre copilot (E1, `contact-centre-conversations`) publishes a `kind: disclosure`
pack and its file says the shape is shared with this repo on purpose. The division of labour is
real: the copilot reminds an agent that a window is open, and this scorecard grades whether the
reminder worked. If each read its own artifact, a bank could tighten a wording in one and grade
against the other, and the QA answer would depend on which system you asked.

So this repo READS that pack rather than re-authoring it. The fixture below is a byte copy of
E1's shipped file, so a change to the sibling shape breaks this suite instead of being noticed
in production.

What the reader deliberately does NOT do:

* it does not invent a regulator instrument. E1's shape names none, so the citation records what
  is actually known: which reviewed artifact, maintained under which regulator. Ordered script
  segments and named instruments stay in this repo's own pack, which is why a sibling pack is a
  supplement and never a replacement.
* it does not guess at another document kind, and it does not pick a winner when the two packs
  configure the same obligation. Both refuse.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from conversation_qa_scorecard.adapters.local.transcription import (
    FixtureTranscriptSource,
)
from conversation_qa_scorecard.config import (
    Settings,
)
from conversation_qa_scorecard.domain.errors import (
    ScorePackError,
)
from conversation_qa_scorecard.domain.ingestion import (
    redact_for_scoring,
)
from conversation_qa_scorecard.domain.models import (
    RequirementKind,
    RequirementStatus,
    ScoringRequest,
)
from conversation_qa_scorecard.domain.scoring_engine import (
    ScoringEngine,
)
from conversation_qa_scorecard.ports.speech import ChannelRole
from conversation_qa_scorecard.score_pack import (
    SIBLING_DISCLOSURE_KIND,
    load_packs,
    load_sibling_disclosures,
    with_sibling_disclosures,
)

from tests import REPO_ROOT
from tests.fixtures import sample_cases

SIBLING = REPO_ROOT / "tests" / "fixtures" / "packs" / "sibling-disclosure-sg-retail.yaml"
_AS_OF = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)
_SETTINGS = Settings(profile="local", audit_path=":memory:", scorecard_path=":memory:")


def _document() -> dict:
    return yaml.safe_load(SIBLING.read_text(encoding="utf-8"))


def _written(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "sibling.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


def test_the_fixture_is_the_shape_the_sibling_actually_publishes() -> None:
    document = _document()
    assert document["kind"] == SIBLING_DISCLOSURE_KIND
    assert document["pack_id"] and document["market"] and document["locale"]
    for entry in document["disclosures"]:
        assert {"disclosure_id", "required_phrase", "role", "severity"} <= set(entry)


def test_every_sibling_disclosure_becomes_a_single_step_requirement() -> None:
    requirements = load_sibling_disclosures(SIBLING)
    assert requirements
    for requirement in requirements:
        assert requirement.kind is RequirementKind.DISCLOSURE
        assert len(requirement.steps) == 1, "a live reminder has one wording, not a sequence"
        assert requirement.role is ChannelRole.AGENT
        assert requirement.citation.source_id.startswith("pack:")
        assert requirement.remediation


def test_the_paraphrases_the_sibling_accepts_are_the_ones_this_engine_accepts() -> None:
    """The whole point: the same wording set, not a second one that happens to look similar."""
    document = _document()
    by_id = {r.requirement_id.split(":", 1)[1]: r for r in load_sibling_disclosures(SIBLING)}
    for entry in document["disclosures"]:
        variants = by_id[entry["disclosure_id"]].steps[0].variants
        texts = {variant.text for variant in variants}
        assert entry["required_phrase"] in texts
        for paraphrase in entry.get("paraphrases") or []:
            assert paraphrase in texts
        assert sum(1 for variant in variants if variant.required) == 1


def test_the_citation_records_the_artifact_and_does_not_invent_an_instrument() -> None:
    citation = load_sibling_disclosures(SIBLING)[0].citation
    assert "sg-retail-disclosures-v1" in citation.source_id
    assert "MAS" in citation.title, "the maintaining regulator is known and is recorded"
    assert "names no regulator publication" in citation.snippet


def test_a_sibling_window_is_read_as_a_deadline_from_the_start_of_the_call() -> None:
    document = _document()
    by_id = {r.requirement_id.split(":", 1)[1]: r for r in load_sibling_disclosures(SIBLING)}
    for entry in document["disclosures"]:
        if entry.get("within_ms"):
            assert by_id[entry["disclosure_id"]].deadline_ms == entry["within_ms"]


def test_the_merged_pack_scores_a_real_contact_and_the_engine_never_learns_which_pack() -> None:
    """The supplement is data, so the engine treats a sibling obligation like any other."""
    native = load_packs()[sample_cases.SG_PACK_ID]
    merged = with_sibling_disclosures(native, SIBLING)
    assert len(merged.requirements) > len(native.requirements)

    source = FixtureTranscriptSource(_SETTINGS)
    contact = next(c for c in source.contacts() if c.contact_id == sample_cases.COMPLIANT_CONTACT)
    declared = tuple(
        r.requirement_id for r in merged.requirements if r.requirement_id.startswith("sg-retail")
    )
    redacted = redact_for_scoring(source.fetch(contact.contact_id))
    scorecard = ScoringEngine().score(
        ScoringRequest(
            contact=contact.__class__(
                contact_id=contact.contact_id,
                tenant=contact.tenant,
                market=contact.market,
                product=contact.product,
                declared_requirement_ids=declared,
                locale=contact.locale,
            ),
            pack=merged,
            as_of=_AS_OF,
        ),
        redacted.transcript,
    )
    statuses = {f.requirement_id: f.status for f in scorecard.findings}
    assert set(statuses) == set(declared)
    recording = statuses["sg-retail-disclosures-v1:recording_notice"]
    assert recording is RequirementStatus.PRESENT, (
        "the shipped transcript says 'this call may be recorded', which the SIBLING pack lists "
        "as an accepted paraphrase; if this fails the two systems have stopped agreeing"
    )


def test_a_pack_of_another_kind_refuses_rather_than_guessing_at_its_fields(
    tmp_path: Path,
) -> None:
    document = _document()
    document["kind"] = "procedure"
    with pytest.raises(ScorePackError, match="accepts only"):
        load_sibling_disclosures(_written(tmp_path, document))


def test_a_sibling_disclosure_with_no_role_refuses(tmp_path: Path) -> None:
    document = _document()
    document["disclosures"][0].pop("role")
    with pytest.raises(ScorePackError, match="role"):
        load_sibling_disclosures(_written(tmp_path, document))


def test_a_sibling_disclosure_with_no_wording_refuses(tmp_path: Path) -> None:
    document = _document()
    document["disclosures"][0].pop("required_phrase")
    with pytest.raises(ScorePackError, match="required_phrase"):
        load_sibling_disclosures(_written(tmp_path, document))


def test_an_empty_supplement_refuses_rather_than_adding_nothing_quietly(
    tmp_path: Path,
) -> None:
    document = _document()
    document["disclosures"] = []
    with pytest.raises(ScorePackError, match="empty supplement"):
        load_sibling_disclosures(_written(tmp_path, document))


def test_an_obligation_configured_in_both_packs_refuses_rather_than_picking_a_winner(
    tmp_path: Path,
) -> None:
    """Two artifacts configuring one obligation is the drift the shared shape exists to prevent."""
    native = load_packs()[sample_cases.SG_PACK_ID]
    document = _document()
    document["pack_id"] = "sg-retail-banking-v1"
    document["disclosures"][0]["disclosure_id"] = "collision"
    colliding = with_sibling_disclosures(native, _written(tmp_path, document)).requirements
    assert any(r.requirement_id == "sg-retail-banking-v1:collision" for r in colliding)

    second = _document()
    second["pack_id"] = "sg-retail-banking-v1"
    second["disclosures"][0]["disclosure_id"] = "collision"
    once = with_sibling_disclosures(native, _written(tmp_path, second))
    with pytest.raises(ScorePackError, match="redefines"):
        with_sibling_disclosures(once, _written(tmp_path, second))
