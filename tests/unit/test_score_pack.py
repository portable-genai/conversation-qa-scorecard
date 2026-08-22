"""The score pack is CONFIGURATION, and it is loaded fail-closed.

The pack is the only thing between a mandated wording and a scorecard, so a half-parsed one is
worse than no pack at all: it reports an unconfigured obligation as a pass. Every test below is
a refusal the loader makes, and each of them names a scorecard it prevents.

The other half of this suite is the B4 claim: nothing about a market lives in code. The engine
never learns what Singapore requires, so the same binary scores a different jurisdiction by
being handed a different file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conversation_qa_scorecard.config import (
    Settings,
)
from conversation_qa_scorecard.domain.errors import (
    ScorePackError,
)
from conversation_qa_scorecard.domain.models import (
    Market,
    RequirementKind,
)
from conversation_qa_scorecard.score_pack import (
    DEFAULT_PACK_PATH,
    load_packs,
    pack_for_market,
    packs_for,
)

from tests.fixtures import sample_cases

_SHIPPED = yaml.safe_load(DEFAULT_PACK_PATH.read_text(encoding="utf-8"))


def _written(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "pack.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


def _shipped_copy() -> dict:
    return yaml.safe_load(DEFAULT_PACK_PATH.read_text(encoding="utf-8"))


def _first_requirement(document: dict, pack_id: str = sample_cases.SG_PACK_ID) -> dict:
    requirement = document["packs"][pack_id]["requirements"][0]
    assert isinstance(requirement, dict)
    return requirement


# --------------------------------------------------------------------------------------- #
# The shipped pack is well formed and complete
# --------------------------------------------------------------------------------------- #
def test_the_shipped_reference_pack_loads_and_covers_every_market_in_scope() -> None:
    packs = load_packs()
    assert {pack.market for pack in packs.values()} == {Market.SG, Market.AU, Market.JP}
    for pack in packs.values():
        assert pack.requirements and pack.cues and pack.version


def test_every_requirement_names_an_instrument_and_binds_a_speaker() -> None:
    for pack in load_packs().values():
        for requirement in pack.requirements:
            assert requirement.citation.source_id and requirement.citation.title
            assert requirement.role is not None
            assert requirement.steps
            assert requirement.remediation, "a failing obligation must say what to do about it"


def test_a_step_carries_the_mandated_wording_and_its_approved_paraphrases() -> None:
    """Acceptable paraphrases are configuration, which is the whole point of the pack file."""
    pack = load_packs()[sample_cases.SG_PACK_ID]
    recording = next(r for r in pack.requirements if r.requirement_id == "SG-DISC-RECORDING")
    variants = recording.steps[0].variants
    assert sum(1 for variant in variants if variant.required) == 1
    assert len(variants) > 1, "a pack with no paraphrases makes the matcher the policy"


def test_an_ordering_constraint_is_data_on_the_requirement() -> None:
    pack = load_packs()[sample_cases.SG_PACK_ID]
    script = next(r for r in pack.requirements if r.kind is RequirementKind.SCRIPT_SEGMENT)
    assert [step.entry_id for step in script.steps] == [
        "purpose_of_call",
        "risk_warning",
        "fee_disclosure",
        "consent_to_proceed",
    ]
    assert script.within_ms and script.within_ms > 0


def test_the_active_pack_is_selected_by_configuration_not_by_code(tmp_path: Path) -> None:
    document = _shipped_copy()
    document["version"] = "adopter-2027-01"
    override = _written(tmp_path, document)
    assert (
        packs_for(Settings(profile="local", score_pack_path=str(override)))[
            sample_cases.SG_PACK_ID
        ].version
        == "adopter-2027-01"
    )


def test_the_market_lookup_refuses_rather_than_borrowing_another_markets_pack() -> None:
    with pytest.raises(ScorePackError, match="no pack is configured"):
        pack_for_market(Settings(profile="local"), Market.SG, "not_a_configured_product")


# --------------------------------------------------------------------------------------- #
# The refusals, one per scorecard they prevent
# --------------------------------------------------------------------------------------- #
def test_a_missing_pack_file_refuses(tmp_path: Path) -> None:
    with pytest.raises(ScorePackError, match="does not exist"):
        load_packs(tmp_path / "absent.yaml")


def test_a_pack_with_no_version_refuses(tmp_path: Path) -> None:
    """A scorecard has to be able to record what scored it."""
    document = _shipped_copy()
    document.pop("version")
    with pytest.raises(ScorePackError, match="version"):
        load_packs(_written(tmp_path, document))


def test_a_requirement_with_no_citation_refuses(tmp_path: Path) -> None:
    """A finding with no named instrument is not a compliance finding."""
    document = _shipped_copy()
    _first_requirement(document).pop("citation")
    with pytest.raises(ScorePackError, match="citation"):
        load_packs(_written(tmp_path, document))


def test_a_requirement_citing_an_undefined_instrument_refuses(tmp_path: Path) -> None:
    document = _shipped_copy()
    _first_requirement(document)["citation"] = "an_instrument_nobody_defined"
    with pytest.raises(ScorePackError, match="not defined in the pack"):
        load_packs(_written(tmp_path, document))


def test_a_requirement_with_no_role_refuses(tmp_path: Path) -> None:
    """It would be satisfied by the customer reading the risk warning back."""
    document = _shipped_copy()
    _first_requirement(document).pop("role")
    with pytest.raises(ScorePackError, match="role"):
        load_packs(_written(tmp_path, document))


def test_a_requirement_naming_an_undefined_step_refuses(tmp_path: Path) -> None:
    """A step with no wording can never match, so it would be a silent permanent absence."""
    document = _shipped_copy()
    _first_requirement(document)["steps"] = ["a_step_with_no_wording"]
    with pytest.raises(ScorePackError, match="not defined in this pack"):
        load_packs(_written(tmp_path, document))


def test_a_duplicate_requirement_id_refuses(tmp_path: Path) -> None:
    """Two results under one id make the scorecard ambiguous, and that is not a record."""
    document = _shipped_copy()
    requirements = document["packs"][sample_cases.SG_PACK_ID]["requirements"]
    requirements.append(dict(requirements[0]))
    with pytest.raises(ScorePackError, match="duplicate requirement id"):
        load_packs(_written(tmp_path, document))


def test_a_step_with_no_mandated_wording_refuses(tmp_path: Path) -> None:
    document = _shipped_copy()
    document["packs"][sample_cases.SG_PACK_ID]["phrases"]["recording_notice"].pop("required_phrase")
    with pytest.raises(ScorePackError, match="required_phrase"):
        load_packs(_written(tmp_path, document))


def test_an_empty_paraphrase_refuses_because_it_would_match_every_turn(tmp_path: Path) -> None:
    document = _shipped_copy()
    document["packs"][sample_cases.SG_PACK_ID]["phrases"]["recording_notice"]["paraphrases"] = [
        "   "
    ]
    with pytest.raises(ScorePackError, match="empty paraphrase"):
        load_packs(_written(tmp_path, document))


def test_a_cue_family_with_no_phrases_refuses(tmp_path: Path) -> None:
    document = _shipped_copy()
    document["packs"][sample_cases.SG_PACK_ID]["cues"]["financial_hardship"]["phrases"] = []
    with pytest.raises(ScorePackError, match="never fires"):
        load_packs(_written(tmp_path, document))


def test_an_unsupported_locale_refuses_at_load_rather_than_matching_nothing(
    tmp_path: Path,
) -> None:
    document = _shipped_copy()
    document["packs"][sample_cases.SG_PACK_ID]["locale"] = "not a locale"
    with pytest.raises(ScorePackError, match="locale"):
        load_packs(_written(tmp_path, document))


def test_a_non_positive_timing_window_refuses(tmp_path: Path) -> None:
    document = _shipped_copy()
    _first_requirement(document)["deadline_ms"] = 0
    with pytest.raises(ScorePackError, match="never opens"):
        load_packs(_written(tmp_path, document))


def test_an_unknown_severity_refuses_rather_than_defaulting(tmp_path: Path) -> None:
    document = _shipped_copy()
    _first_requirement(document)["severity"] = "catastrophic"
    with pytest.raises(ScorePackError, match="unknown severity"):
        load_packs(_written(tmp_path, document))


def test_malformed_yaml_refuses_with_the_file_named(tmp_path: Path) -> None:
    path = tmp_path / "pack.yaml"
    path.write_text("packs: [unclosed\n", encoding="utf-8")
    with pytest.raises(ScorePackError, match="not valid YAML"):
        load_packs(path)


# --------------------------------------------------------------------------------------- #
# Synthetic-data hygiene
# --------------------------------------------------------------------------------------- #
def test_the_pack_states_where_its_real_names_are_and_carries_no_customer_data() -> None:
    """Instrument names are the one place real-world names appear, and the file says so."""
    text = DEFAULT_PACK_PATH.read_text(encoding="utf-8")
    assert "citations" in _SHIPPED
    assert "real, named regulator publications" in text
    assert "obviously\n# fictional synthetic data" in text
    for real_looking in ("@gmail.", "@outlook.", "@yahoo."):
        assert real_looking not in text
