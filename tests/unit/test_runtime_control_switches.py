"""Review routing has a switch, default on, and every caller says what happened to a hand-off.

The fleet's runtime-control contract (2026-09-24). Review routing is the one cheap runtime
control this service has: ``CONVQA_REVIEW_ROUTING`` is read in three states; off binds a
disabled router and says so at startup; on under the managed profile refuses to boot without a
console; and the API, the agent tool and the CLI report ``review_routing`` rather than failing
an already-scored contact when the console is unreachable. Routing runs inside the domain
service, so each surface passes its own recorder in through ``service.build_service``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conversation_qa_scorecard.adapters.controls import (
    DisabledReviewRouter,
    RecordingReviewRouter,
    ReviewRouting,
)
from conversation_qa_scorecard.agent import tools
from conversation_qa_scorecard.cli.main import main as cli_main
from conversation_qa_scorecard.config import (
    REVIEW_ROUTING_ENV,
    Container,
    ControlSwitches,
    ProfileChoice,
    Settings,
    build_container,
    warn_switched_off,
)
from conversation_qa_scorecard.domain.models import Scorecard
from conversation_qa_scorecard.envread import ConfiguredEmptyError
from conversation_qa_scorecard.service import build_service, pack_for_contact

from tests.conftest import LOOPBACK_PEER, local_settings, reimport
from tests.fixtures import sample_cases

_AUDITOR = {"X-Dev-Persona": "auditor"}
_LOCAL_ROUTE = "conversation_qa_scorecard.adapters.local.review_router.LocalReviewRouter.route"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REVIEW_ROUTING_ENV, raising=False)
    monkeypatch.delenv("HUMAN_REVIEW_URL", raising=False)


def _client() -> TestClient:
    """A fresh local app, so its per-process container reads this test's posture."""
    return TestClient(reimport("conversation_qa_scorecard.api.app").app, client=LOOPBACK_PEER)


def _scorecard(contact_id: str = sample_cases.BREACH_CONTACT) -> Scorecard:
    container = build_container(local_settings())
    contact = next(c for c in container.transcription.contacts() if c.contact_id == contact_id)
    return build_service(container).score_contact(
        contact,
        pack_for_contact(container, contact),
        actor=sample_cases.ACTOR,
        tenant=sample_cases.TENANT,
        as_of=datetime(2026, 9, 1, tzinfo=UTC),
    )


def _managed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "conversation_qa_scorecard.config.resolve_profile",
        lambda environ=None: ProfileChoice(profile="gcp", explicit=True),
    )


class _Accepting:
    def route(self, scorecard: Scorecard, *, maker: str, tenant: str = "") -> str:
        return "review-1"


class _Refusing:
    def route(self, scorecard: Scorecard, *, maker: str, tenant: str = "") -> str:
        raise ConnectionError("console unreachable")


# --------------------------------------------------------------------------- #
# Three states
# --------------------------------------------------------------------------- #
def test_routing_is_on_when_nothing_is_said() -> None:
    assert Settings.load().controls == ControlSwitches(review_routing=True)


def test_routing_switched_off_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REVIEW_ROUTING_ENV, "off")
    assert Settings.load().controls.switched_off() == (REVIEW_ROUTING_ENV,)


def test_an_emptied_switch_refuses_at_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REVIEW_ROUTING_ENV, "")
    with pytest.raises(ConfiguredEmptyError, match=REVIEW_ROUTING_ENV):
        Settings.load()


def test_an_unrecognised_switch_refuses_at_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REVIEW_ROUTING_ENV, "sometimes")
    with pytest.raises(ValueError, match=REVIEW_ROUTING_ENV):
        Settings.load()


# --------------------------------------------------------------------------- #
# Off binds the disabled router, and says so once
# --------------------------------------------------------------------------- #
def test_off_binds_the_disabled_router() -> None:
    settings = local_settings(controls=ControlSwitches(review_routing=False))
    assert isinstance(Container(settings).review_router, DisabledReviewRouter)


def test_on_binds_the_profile_router() -> None:
    assert not isinstance(Container(local_settings()).review_router, DisabledReviewRouter)


def test_the_off_posture_is_logged_once_however_many_containers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    warn_switched_off.cache_clear()
    settings = local_settings(controls=ControlSwitches(review_routing=False))
    with caplog.at_level(logging.WARNING, logger="conversation_qa_scorecard.config"):
        for _ in range(3):
            build_container(settings)
    assert caplog.text.count(REVIEW_ROUTING_ENV) == 1


# --------------------------------------------------------------------------- #
# On has to work: checked at boot under the managed profile
# --------------------------------------------------------------------------- #
def test_routing_on_under_gcp_without_a_console_refuses_at_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _managed(monkeypatch)
    with pytest.raises(ConfiguredEmptyError, match="HUMAN_REVIEW_URL"):
        Settings.load()


def test_routing_stated_off_under_gcp_needs_no_console(monkeypatch: pytest.MonkeyPatch) -> None:
    _managed(monkeypatch)
    monkeypatch.setenv(REVIEW_ROUTING_ENV, "false")
    assert Settings.load().controls.review_routing is False


def test_routing_on_under_gcp_with_a_console_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    _managed(monkeypatch)
    monkeypatch.setenv("HUMAN_REVIEW_URL", "https://review.example.test")
    assert Settings.load().review_url == "https://review.example.test"


# --------------------------------------------------------------------------- #
# The four routing outcomes
# --------------------------------------------------------------------------- #
def test_routing_outcomes_take_each_of_their_four_values() -> None:
    scorecard = _scorecard()
    assert scorecard.requires_human_review

    unrequired = RecordingReviewRouter(_Accepting())
    clean = dataclasses.replace(scorecard, requires_human_review=False)
    assert unrequired.route(clean, maker="m") == ""
    assert unrequired.outcome is ReviewRouting.NOT_REQUIRED

    routed = RecordingReviewRouter(_Accepting())
    assert routed.route(scorecard, maker="m") == "review-1"
    assert routed.outcome is ReviewRouting.ROUTED

    off = RecordingReviewRouter(DisabledReviewRouter(local_settings()))
    assert off.route(scorecard, maker="m") == ""
    assert off.outcome is ReviewRouting.OFF

    failed = RecordingReviewRouter(_Refusing())
    assert failed.route(scorecard, maker="m") == ""
    assert failed.outcome is ReviewRouting.FAILED


def test_a_failed_hand_off_is_reported_and_logged_never_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failed = RecordingReviewRouter(_Refusing())
    with caplog.at_level(logging.WARNING, logger="conversation_qa_scorecard.adapters.controls"):
        assert failed.route(_scorecard(), maker="m") == ""
    assert failed.outcome is ReviewRouting.FAILED
    assert "ConnectionError" in caplog.text


# --------------------------------------------------------------------------- #
# Every caller reports it: the API, the agent tool, the CLI
# --------------------------------------------------------------------------- #
def _score(client: TestClient, contact_id: str = sample_cases.BREACH_CONTACT) -> Any:
    return client.post("/v1/scorecards", json={"contact_id": contact_id}, headers=_AUDITOR)


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with _client() as c:
        yield c


def test_the_api_reports_a_routed_hand_off(client: TestClient) -> None:
    body = _score(client).json()
    assert body["review_routing"] == "routed"
    assert body["review_ref"]


def test_the_api_reports_nothing_to_route(client: TestClient) -> None:
    body = _score(client, sample_cases.COMPLIANT_CONTACT).json()
    assert body["requires_human_review"] is False
    assert body["review_routing"] == "not_required"


def test_a_stored_scorecard_read_back_claims_no_routing_outcome(client: TestClient) -> None:
    scored = _score(client).json()
    read = client.get(f"/v1/scorecards/{scored['scorecard_id']}", headers=_AUDITOR).json()
    assert read["review_routing"] is None
    assert read["review_ref"] == scored["review_ref"]


def test_the_api_reports_routing_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REVIEW_ROUTING_ENV, "off")
    with _client() as client:
        body = _score(client).json()
    assert body["review_routing"] == "off"
    assert body["review_ref"] == ""


def test_the_api_reports_a_failed_hand_off_instead_of_failing_the_request(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setattr(_LOCAL_ROUTE, _Refusing.route)
    response = _score(client)
    assert response.status_code == 200
    assert response.json()["review_routing"] == "failed"
    assert response.json()["review_ref"] == ""


def test_the_agent_tool_reports_the_hand_off(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = local_settings()
    payload = tools.score_contact(
        sample_cases.BREACH_CONTACT, sample_cases.TENANT, settings=settings
    )
    assert payload["review_routing"] == "routed"

    monkeypatch.setattr(_LOCAL_ROUTE, _Refusing.route)
    failed = tools.score_contact(
        sample_cases.BREACH_CONTACT, sample_cases.TENANT, settings=settings
    )
    assert failed["review_routing"] == "failed"
    assert failed["review_ref"] == ""


def test_the_cli_reports_the_hand_off(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["score", sample_cases.BREACH_CONTACT]) == 0
    assert "human review hand-off : routed" in capsys.readouterr().out

    monkeypatch.setattr(_LOCAL_ROUTE, _Refusing.route)
    assert cli_main(["score", sample_cases.BREACH_CONTACT, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["review_routing"] == "failed"
