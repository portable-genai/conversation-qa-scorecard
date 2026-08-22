"""API surface: verified-principal identity, the tenant boundary, fail-closed S2S, headers.

The client comes from the shared ``api_client`` fixture, which pins a loopback peer: the
app-object exposure guard refuses the unauthenticated local posture to any other peer, and
TestClient's default peer is the literal host "testclient".

The load-bearing assertions here are the two the API is the ONLY place to prove:

* the tenant used for authorisation is the VERIFIED principal's, not anything in the body, and
* another tenant's scorecard answers 403 and not 404, because the record exists and answering
  404 would make the store probeable with an id generator.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.fixtures import sample_cases

_TOKEN_ENV = "CONVQA_S2S_TOKEN"


def _score(client: TestClient, contact_id: str, persona: str = "auditor") -> dict:
    return client.post(
        "/v1/scorecards",
        json={"contact_id": contact_id},
        headers={"X-Dev-Persona": persona},
    ).json()


def test_a_failing_contact_is_scored_and_routed_in_the_same_request(
    api_client: TestClient,
) -> None:
    resp = api_client.post(
        "/v1/scorecards",
        json={"contact_id": sample_cases.BREACH_CONTACT},
        headers={"X-Dev-Persona": "auditor"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["disposition"] == "non_compliant"
    assert body["severity"] == "critical"
    assert body["requires_human_review"] is True
    # Rule R8: the escalation was routed, not merely flagged (see test_review_routing.py).
    assert body["review_ref"]
    assert body["engine_version"], "a compliance record must say which engine produced it"


def test_every_finding_carries_a_citation_and_a_failing_one_carries_a_reason(
    api_client: TestClient,
) -> None:
    body = _score(api_client, sample_cases.BREACH_CONTACT)
    assert body["findings"], "a scorecard with no findings is not a scorecard"
    for finding in body["findings"]:
        assert finding["citation"]["source_id"], "a finding with no instrument is not a finding"
        if finding["status"] == "present":
            assert finding["evidence"], "a satisfied finding must cite the words that satisfied it"
            span = finding["evidence"][0]
            assert span["char_end"] > span["char_start"]
            assert span["text"], "an evidence span with no text cannot be shown to a reviewer"
        else:
            assert finding["detail"], "a failure must say why"


def test_a_passing_contact_manufactures_no_review(api_client: TestClient) -> None:
    body = _score(api_client, sample_cases.COMPLIANT_CONTACT)
    assert body["disposition"] == "compliant"
    assert body["requires_human_review"] is False
    assert body["review_ref"] == "", "a passing scorecard must not manufacture a review"


def test_the_scorecard_can_be_read_back_by_its_owning_tenant(api_client: TestClient) -> None:
    scorecard_id = _score(api_client, sample_cases.BREACH_CONTACT)["scorecard_id"]
    resp = api_client.get(f"/v1/scorecards/{scorecard_id}", headers={"X-Dev-Persona": "auditor"})
    assert resp.status_code == 200
    assert resp.json()["scorecard_id"] == scorecard_id


def test_another_tenants_scorecard_is_403_and_not_404(api_client: TestClient) -> None:
    """The record EXISTS and this caller may not have it. 404 would make the store probeable."""
    scorecard_id = _score(api_client, sample_cases.BREACH_CONTACT)["scorecard_id"]
    resp = api_client.get(
        f"/v1/scorecards/{scorecard_id}", headers={"X-Dev-Persona": "other-tenant"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"], "a refusal must say what it refused"


def test_an_unknown_scorecard_is_404(api_client: TestClient) -> None:
    resp = api_client.get("/v1/scorecards/sc-nope", headers={"X-Dev-Persona": "auditor"})
    assert resp.status_code == 404


def test_scoring_another_tenants_contact_is_refused_before_anything_is_transcribed(
    api_client: TestClient,
) -> None:
    resp = api_client.post(
        "/v1/scorecards",
        json={"contact_id": sample_cases.OTHER_TENANT_CONTACT},
        headers={"X-Dev-Persona": "auditor"},
    )
    assert resp.status_code == 403


def test_the_contact_list_is_filtered_on_the_verified_tenant(api_client: TestClient) -> None:
    mine = api_client.get("/v1/contacts", headers={"X-Dev-Persona": "auditor"}).json()
    theirs = api_client.get("/v1/contacts", headers={"X-Dev-Persona": "other-tenant"}).json()
    assert {c["contact_id"] for c in mine}
    assert sample_cases.OTHER_TENANT_CONTACT not in {c["contact_id"] for c in mine}
    assert {c["contact_id"] for c in theirs} == {sample_cases.OTHER_TENANT_CONTACT}


def test_the_transcript_a_reviewer_reads_is_the_redacted_one(api_client: TestClient) -> None:
    """The same ingestion call the engine scored, so a citation resolves to the same characters."""
    body = api_client.get(
        f"/v1/contacts/{sample_cases.PII_CONTACT}/transcript",
        headers={"X-Dev-Persona": "auditor"},
    ).json()
    rendered = repr(body)
    assert sample_cases.PLANTED_NRIC not in rendered
    assert body["redaction_count"] >= 1
    assert body["turns"], "a timeline needs turns"


def test_another_tenants_transcript_is_refused(api_client: TestClient) -> None:
    resp = api_client.get(
        f"/v1/contacts/{sample_cases.OTHER_TENANT_CONTACT}/transcript",
        headers={"X-Dev-Persona": "auditor"},
    )
    assert resp.status_code == 403


def test_unknown_persona_is_401(api_client: TestClient) -> None:
    resp = api_client.post(
        "/v1/scorecards",
        json={"contact_id": sample_cases.COMPLIANT_CONTACT},
        headers={"X-Dev-Persona": "ghost"},
    )
    assert resp.status_code == 401


def test_healthz_reports_profile_and_region(api_client: TestClient) -> None:
    body = api_client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["profile"] == "local"
    assert body["region"] == "asia-southeast1"


def test_security_headers_present(api_client: TestClient) -> None:
    headers = api_client.get("/healthz").headers
    assert headers["Content-Security-Policy"] == "frame-ancestors 'self'"
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.fixture()
def token_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv(_TOKEN_ENV, "s3cret-service-token")
    yield "s3cret-service-token"


def test_s2s_endpoint_open_when_secret_unset(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_TOKEN_ENV, raising=False)
    assert api_client.post("/v1/audit/ping").status_code == 200


def test_s2s_endpoint_rejects_missing_token_when_enforced(
    api_client: TestClient, token_env: str
) -> None:
    assert api_client.post("/v1/audit/ping").status_code == 401


def test_s2s_endpoint_accepts_correct_token(api_client: TestClient, token_env: str) -> None:
    resp = api_client.post("/v1/audit/ping", headers={"Authorization": f"Bearer {token_env}"})
    assert resp.status_code == 200
