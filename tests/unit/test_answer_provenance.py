"""The service half of the model pills: which model ANSWERED, and whether it searched.

The console shows two pills at the top right: the model that answered the last request, and
``Search`` when that answer used an online search tool. Both come from response headers the kit
emits (``install_answer_provenance`` in ``api/app.py``) for whatever the model adapters NOTED as
they called. Before a request is answered the pill shows ``generator_model`` from ``/healthz``,
so that value must be the model the bound adapter calls, never one a configuration flag names
while the adapter calls another.

The Gemini adapters are driven here through a FAKE ``google.genai`` module, so what is proved is
this repository's half: the model id each call notes, and the sampling each call sends. Both
managed calls are free (no temperature at all): narration restates decided figures in prose, and
the advisory classifier writes a sentence of colour whose kind and confidence are fixed in code.
Neither attaches an online search tool, so neither may claim a search.
"""

from __future__ import annotations

import dataclasses
import sys
import types
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hex_service_kit import provenance

from conversation_qa_scorecard import config
from conversation_qa_scorecard.adapters.gcp.narration import GeminiNarrator
from conversation_qa_scorecard.adapters.gcp.signals import GeminiSignalClassifier
from conversation_qa_scorecard.adapters.local.narration import STUB_MODEL, LocalNarrator
from conversation_qa_scorecard.adapters.local.signals import (
    STUB_MODEL as SIGNALS_STUB_MODEL,
)
from conversation_qa_scorecard.config import Settings
from conversation_qa_scorecard.ports.narration import NarrationBrief
from conversation_qa_scorecard.ports.signals import SignalRequest

from tests import REPO_ROOT
from tests.conftest import LOOPBACK_PEER, local_settings
from tests.fixtures import sample_cases

ANSWERED_BY = "x-answered-by"
SEARCH_USED = "x-search-used"


@pytest.fixture()
def local_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The API under ``local``, set here: the CI targets run the suite with no profile exported."""
    monkeypatch.setenv("CONVQA_PROFILE", "local")
    from conversation_qa_scorecard.api.app import app

    with TestClient(app, client=LOOPBACK_PEER) as client:
        yield client


def _score(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/v1/scorecards",
        json={"contact_id": sample_cases.BREACH_CONTACT},
        headers={"X-Dev-Persona": "auditor"},
    )
    assert response.status_code == 200, response.text
    return dict(response.headers)


def test_the_local_stub_answers_as_the_model_the_pill_first_names(
    local_client: TestClient,
) -> None:
    """Under ``local`` the pill before and after the answer name the same stub."""
    headers = _score(local_client)
    assert headers[ANSWERED_BY] == STUB_MODEL
    assert STUB_MODEL == SIGNALS_STUB_MODEL == local_settings().generator_model
    assert SEARCH_USED not in headers


def test_a_call_that_searched_says_so_and_the_next_request_starts_fresh(
    local_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = LocalNarrator.narrate

    def searching(self: LocalNarrator, brief: NarrationBrief) -> Any:
        provenance.note_model("fake-searching-model")
        provenance.note_search()
        return original(self, brief)

    monkeypatch.setattr(LocalNarrator, "narrate", searching)
    headers = _score(local_client)
    assert "fake-searching-model" in headers[ANSWERED_BY].split(", ")
    assert headers[SEARCH_USED] == "true"
    monkeypatch.setattr(LocalNarrator, "narrate", original)
    headers = _score(local_client)
    assert headers[ANSWERED_BY] == STUB_MODEL
    assert SEARCH_USED not in headers


def test_a_request_no_model_answered_sends_neither_header(local_client: TestClient) -> None:
    response = local_client.get("/healthz")
    assert ANSWERED_BY not in response.headers
    assert SEARCH_USED not in response.headers


# --------------------------------------------------------------------------------------- #
# The Gemini adapters, through a fake SDK.
# --------------------------------------------------------------------------------------- #
class _FakeModels:
    def __init__(self, text: str) -> None:
        self.calls: list[dict[str, Any]] = []
        self._text = text

    def generate_content(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(text=self._text)


def _fake_genai(monkeypatch: pytest.MonkeyPatch, text: str) -> _FakeModels:
    models = _FakeModels(text)
    genai = types.ModuleType("google.genai")
    genai.Client = lambda **_: SimpleNamespace(models=models)  # type: ignore[attr-defined]
    google = sys.modules.get("google") or types.ModuleType("google")
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setattr(google, "genai", genai, raising=False)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return models


def _gcp_settings() -> Settings:
    return dataclasses.replace(local_settings(), profile="gcp")


def _brief() -> NarrationBrief:
    return NarrationBrief(
        scorecard_id="sc-1",
        contact_id=sample_cases.BREACH_CONTACT,
        market="SG",
        disposition="non_compliant",
        severity="critical",
        disclosure_score=0.5,
        adherence_score=0.5,
        sentiment_score=0,
        allowed_figures=frozenset({"0.50"}),
    )


def test_the_gemini_narrator_notes_its_model_and_drafts_with_no_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = _fake_genai(monkeypatch, '{"headline": "h", "body": "b"}')
    settings = _gcp_settings()
    with provenance.scope() as record:
        GeminiNarrator(settings).narrate(_brief())
    assert record.models == [settings.narration_model]
    assert record.search_used is False
    (call,) = models.calls
    assert call["model"] == settings.narration_model
    assert "temperature" not in call["config"], "narration is drafting: free sends none"
    assert "tools" not in call["config"]


def test_an_unparseable_narration_notes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deterministic fallback answers then, not the model, so the model is not named."""
    _fake_genai(monkeypatch, "not json")
    with provenance.scope() as record:
        assert GeminiNarrator(_gcp_settings()).narrate(_brief()) is None
    assert record.models == []


def test_the_advisory_classifier_notes_its_model_samples_free_and_never_searches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = _fake_genai(monkeypatch, '{"note": "The customer sounds anxious."}')
    settings = _gcp_settings()
    request = SignalRequest(contact_id="c-1", locale="en-SG", customer_utterances=("help",))
    with provenance.scope() as record:
        notes = GeminiSignalClassifier(settings).classify(request)
    assert notes and notes[0].source == settings.narration_model
    assert record.models == [settings.narration_model]
    assert record.search_used is False
    (call,) = models.calls
    assert "temperature" not in call["config"]
    assert "tools" not in call["config"]


# --------------------------------------------------------------------------------------- #
# generator_model is the model the adapter calls.
# --------------------------------------------------------------------------------------- #
def test_generator_model_is_the_setting_the_gemini_adapters_read() -> None:
    settings = _gcp_settings()
    assert settings.generator_model == settings.narration_model


def test_no_flag_swaps_in_a_model_the_adapter_never_calls() -> None:
    """The latent false banner: a flag that moved the pill but not the model that answered."""
    models = SimpleNamespace(
        reasoning="the-model-the-adapter-calls",
        hard_reasoning="a-model-nobody-calls",
        use_hard_reasoning=True,
    )
    named = config._model_from_settings(SimpleNamespace(models=models), "models.reasoning")
    assert named == "the-model-the-adapter-calls"


def test_the_hard_reasoning_flag_is_gone() -> None:
    for relative in ("config/settings.yaml", "src/conversation_qa_scorecard/config.py"):
        assert "use_hard_reasoning" not in (REPO_ROOT / relative).read_text(encoding="utf-8")
